"""
FLIP-STAR strategy -- runs ALONGSIDE Pulse's other entry rules.

Uses the exact same numbers the Crypto Edge Board dashboard shows (it reads the board's /api/state), so what the
bot does matches what you see on the top coin boxes:

  ENTRY (2 shares of the favored side) when, with 8 minutes or less left in the 15-minute window:
      the coin has the RED STAR, or its "To flip" line is GREEN (price 2+ sigma on the right side of the target).
  HOLD while "To flip" is yellow (1-2 sigma).
  EXIT as soon as "To flip" turns RED (under 1 sigma).

If the board can't be reached (or its data is stale) the bot opens nothing new, and any flip-star position it holds
falls back to Pulse's normal stop-loss until fresh numbers come back. Pulse's hard backstop (78%) still applies.
"""
import os
import time
import uuid

import requests

FLIP_ENABLED = True
FLIP_SHARES = 2
FLIP_MAX_SECONDS_LEFT = 480        # only the last 8 minutes of the window
FLIP_MIN_SECONDS_LEFT = 30         # too close to the bell to get a clean fill
FLIP_ENTRY_SIGMA = 2.0             # "To flip" green on the dashboard
FLIP_EXIT_SIGMA = 1.0              # "To flip" red on the dashboard -> pull out
FLIP_MAX_SPREAD = 0.05             # skip thin, jumpy books
FLIP_MAX_PRICE = 0.99              # never pay more than this per share
FLIP_SLIPPAGE = 0.01               # limit price = ask + 1c (capped at FLIP_MAX_PRICE); order is immediate-or-cancel
FLIP_MAX_ATTEMPTS_PER_TICKER = 3
FLIP_STALE_SECONDS = 15            # board data older than this is not trusted
EDGE_BOARD_URL = os.getenv("EDGE_BOARD_URL", "https://edge-board-production.up.railway.app/api/state")

_board = {"data": {}, "fetched": 0.0, "ok_at": 0.0}
_attempts = {}


def _side_numbers(m: dict, age: float):
    side = m.get("best_side")
    p_yes = m.get("model_p")
    if side not in ("yes", "no") or p_yes is None:
        return None
    p = p_yes if side == "yes" else 1 - p_yes
    lo, hi = m.get("p_lo"), m.get("p_hi")
    worst = None
    if lo is not None and hi is not None:
        worst = lo if side == "yes" else 1 - hi
    z = m.get("z")
    zs = None if z is None else z * (1 if side == "yes" else -1)
    tl = m.get("t_left")
    tl = None if tl is None else tl - age
    spread = m.get("spread")
    lock = (zs is not None and zs >= 2 and p >= 0.95 and (worst is None or worst >= 0.92)
            and tl is not None and tl >= 30 and (spread is None or spread <= 0.03))
    return {"ticker": m.get("ticker"), "coin": m.get("coin"), "side": side, "p": p, "worst": worst, "zs": zs,
            "tl": tl, "spread": spread, "lock": lock, "yes_ask": m.get("yes_ask"), "no_ask": m.get("no_ask"),
            "title": m.get("title", m.get("ticker"))}


def board(force: bool = False) -> dict:
    """ticker -> numbers for every live 15-minute window on the dashboard (cached ~1s). {} when unavailable/stale."""
    now = time.time()
    if not force and now - _board["fetched"] < 1.0:
        return _board["data"] if now - _board["ok_at"] <= FLIP_STALE_SECONDS else {}
    _board["fetched"] = now
    try:
        r = requests.get(EDGE_BOARD_URL, timeout=4, headers={"Cache-Control": "no-store"})
        r.raise_for_status()
        st = r.json()
        age = max(0.0, now - float(st.get("now", now)))
        if age > FLIP_STALE_SECONDS:
            print(f"[FLIP] dashboard data is {age:.0f}s old -- not trusting it.")
            return {}
        out = {}
        for m in st.get("markets", []):
            if m.get("kind") != "15m":
                continue
            n = _side_numbers(m, age)
            if n and n["ticker"]:
                out[n["ticker"]] = n
        _board["data"], _board["ok_at"] = out, now
    except Exception as e:
        print(f"[FLIP] couldn't read the dashboard ({e}) -- no new flip-star entries until it's back.")
    return _board["data"] if now - _board["ok_at"] <= FLIP_STALE_SECONDS else {}


def entry_signal(n: dict) -> bool:
    return (n["zs"] is not None and n["tl"] is not None
            and FLIP_MIN_SECONDS_LEFT <= n["tl"] <= FLIP_MAX_SECONDS_LEFT
            and (n["lock"] or n["zs"] >= FLIP_ENTRY_SIGMA))


def exit_decision(ticker: str):
    """(should_exit, reason) for a flip-star position, or None if the board has no fresh numbers for it
    (caller then falls back to the normal stop-loss)."""
    n = board().get(ticker)
    if not n or n["zs"] is None:
        return None
    if n["zs"] < FLIP_EXIT_SIGMA:
        return True, f"flip_red (To flip fell to {n['zs']:.1f} sigma, under {FLIP_EXIT_SIGMA:.0f}; win {n['p']*100:.0f}%)"
    return False, ""


def _held_count(client, ticker):
    resp = client.get_positions()
    for p in resp.get("market_positions", resp.get("positions", [])):
        if p.get("ticker") == ticker:
            return float(p.get("position_fp", p.get("position", 0)) or 0), float(p.get("market_exposure_dollars", 0) or 0)
    return 0.0, 0.0


def run_entries(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, log_trade, ensure_shard_funds):
    """Open 2-share flip-star positions where the dashboard says so. Called once per scan loop."""
    if not FLIP_ENABLED:
        return
    b = board()
    if not b:
        return
    for ticker, n in b.items():
        if not entry_signal(n):
            continue
        if ticker in state or ticker in excluded_tickers:
            continue
        if _attempts.get(ticker, 0) >= FLIP_MAX_ATTEMPTS_PER_TICKER:
            continue
        side = "bid" if n["side"] == "yes" else "ask"
        try:
            # Live quote straight from Kalshi (the board can lag a second or two).
            mk = client.get_market(ticker).get("market", {})
            ya, yb = mk.get("yes_ask_dollars"), mk.get("yes_bid_dollars")
            if ya is None or yb is None:
                continue
            ya, yb = float(ya), float(yb)
            if ya - yb > FLIP_MAX_SPREAD + 1e-9:
                continue
            cost = ya if side == "bid" else 1 - yb                       # what one share of our side costs now
            if cost <= 0 or cost > FLIP_MAX_PRICE + 1e-9:
                continue
            limit_side_price = min(FLIP_MAX_PRICE, round(cost + FLIP_SLIPPAGE, 2))
            held, _ = _held_count(client, ticker)
            if held != 0:
                continue   # already holding this market (main strategy or a manual bet) -- don't double up
        except Exception as e:
            print(f"[FLIP] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue

        _attempts[ticker] = _attempts.get(ticker, 0) + 1
        why = "RED STAR" if n["lock"] else f"To flip GREEN {n['zs']:.1f} sigma"
        print(f"[FLIP] ENTRY {ticker} ({n['coin']}): {why}, win {n['p']*100:.0f}%, {int(n['tl'])}s left -- "
              f"{'DRY-RUN ' if dry_run else ''}buying {FLIP_SHARES} {n['side'].upper()} up to ${limit_side_price:.2f}")
        if dry_run:
            with state_lock:
                state[ticker] = {"count": FLIP_SHARES, "entry_price": cost, "side": side, "dry_run": True,
                                 "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                                 "flip_star": True}
            continue
        try:
            ensure_shard_funds(client, ticker, limit_side_price * FLIP_SHARES)
            yes_price = limit_side_price if side == "bid" else round(1 - limit_side_price, 2)
            client.create_order(ticker=ticker, client_order_id=f"pulse-flip-{uuid.uuid4()}", side=side,
                                count=str(FLIP_SHARES), price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
        except Exception as e:
            print(f"[FLIP] {ticker}: entry order failed ({e}).")
            if "market_closed" in str(e) or "market_not_found" in str(e):
                excluded_tickers.add(ticker)
            continue
        # Read what actually filled (immediate-or-cancel: whatever didn't fill right away is cancelled).
        filled, exposure = 0.0, 0.0
        for _ in range(4):
            try:
                filled, exposure = _held_count(client, ticker)
            except Exception:
                pass
            if filled != 0:
                break
            time.sleep(0.4)
        count = abs(filled)
        if count < 1e-9:
            print(f"[FLIP] {ticker}: nothing filled at ${limit_side_price:.2f} (price moved) -- will retry if the signal holds.")
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                             "flip_star": True}
        print(f"[FLIP] FILLED {ticker}: {count:g} {n['side'].upper()} @ ${entry_price:.2f} -- holding until To flip turns red.")
        try:
            from types import SimpleNamespace
            log_trade(SimpleNamespace(ticker=ticker, title=n["title"], coin=n["coin"], seconds_remaining=n["tl"],
                                      model_prob=n["p"], edge_pct=None),
                      side, entry_price, count, False, entry_reason="flip_star_red" if n["lock"] else "flip_star_green")
        except Exception as e:
            print(f"[FLIP] trade log failed (non-fatal): {e}")
