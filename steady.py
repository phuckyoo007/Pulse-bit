"""
STEADY strategy (2 Nov 15:06 CT, Matt) -- runs alongside Pulse, using the edge board's own numbers.

For each 15-minute crypto round:
  * Watch it from the start. If the board's pick (UP / DOWN) never changes AND its win % never drops to 49% or lower
    during the first 11 minutes, the round "qualifies". Example: BTC DOWN goes 55% -> 60% -> 70% -> 88%, never flips.
  * Any flip to the other side, or the pick's win % at 49% or lower, disqualifies the round for good.
  * In the last 4 minutes, once the pick's win % reaches 90%, buy 2 shares of that side (still must not have flipped).
  * The position then gets Pulse's normal exits (86% stop, red sell, trailing profit stop, ...).
A round we did not see from (nearly) its start can't be judged, so it is skipped.
"""
import time
import uuid

import flip_star

STEADY_ENABLED = True
STEADY_SHARES = 2
STEADY_WATCH_UNTIL = 240        # the "first 11 minutes" end when 4:00 is left
STEADY_MUST_SEE_FROM = 840      # must have been watching with at least 14:00 left (round opened at 15:00)
STEADY_MIN_WIN = 0.90           # buy once the pick is at 90%+
STEADY_MIN_PRICE = 0.90        # 2 Nov 15:11 CT (Matt): only buy at 90-97c, cheapest (closest to 90c) first
STEADY_MAX_PRICE = 0.97
STEADY_DQ_WIN = 0.49            # pick at 49% or lower -> no longer qualifies
STEADY_MIN_SECONDS_LEFT = 15    # too close to the end to bother
STEADY_MAX_ATTEMPTS = 2

_rounds = {}      # ticker -> {"side", "ok", "first_tl", "why"}
_attempts = {}
_note = {}


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


def _track(b: dict) -> None:
    now = time.time()
    for ticker, n in b.items():
        if "15M" not in ticker.split("-")[0].upper() or n.get("tl") is None:
            continue
        r = _rounds.get(ticker)
        if r is None:
            r = _rounds[ticker] = {"side": n["side"], "ok": True, "first_tl": n["tl"], "why": "", "seen": now}
            if n["tl"] < STEADY_MUST_SEE_FROM:
                r["ok"], r["why"] = False, f"only seen from {int(n['tl'])}s left"
        r["seen"] = now
        if not r["ok"]:
            continue
        if n["side"] != r["side"]:
            r["ok"], r["why"] = False, f"flipped {r['side'].upper()} -> {n['side'].upper()} with {int(n['tl'])}s left"
        elif n["p"] <= STEADY_DQ_WIN + 1e-9:
            r["ok"], r["why"] = False, f"{r['side'].upper()} fell to {n['p']*100:.0f}% with {int(n['tl'])}s left"
        if not r["ok"]:
            _log_once(ticker, f"[STEADY] {ticker} ({n['coin']}): no longer qualifies -- {r['why']}.")
    for t in [t for t, r in _rounds.items() if now - r["seen"] > 1200]:
        _rounds.pop(t, None)
        _attempts.pop(t, None)
        _note.pop(t, None)


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    if not STEADY_ENABLED:
        return
    b = flip_star.board()
    if not b:
        return
    _track(b)
    if flip_star.in_pause():
        return
    cands = []
    for ticker, n in b.items():
        r = _rounds.get(ticker)
        if not r or not r["ok"] or n["side"] != r["side"]:
            continue
        tl = n.get("tl")
        if tl is None or tl > STEADY_WATCH_UNTIL or tl < STEADY_MIN_SECONDS_LEFT:
            continue
        if n["p"] < STEADY_MIN_WIN - 1e-9:
            continue
        if ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        if _attempts.get(ticker, 0) >= STEADY_MAX_ATTEMPTS:
            continue
        if flip_star._is_excluded(n.get("coin"), ticker):
            continue
        side = "bid" if n["side"] == "yes" else "ask"
        try:
            held, _ = flip_star._held_count(client, ticker)
            if held != 0:
                continue
            mk = client.get_market(ticker).get("market", {})
            ya, yb = mk.get("yes_ask_dollars"), mk.get("yes_bid_dollars")
            if ya is None or yb is None:
                continue
            ya, yb = float(ya), float(yb)
            if ya - yb > flip_star.FLIP_MAX_SPREAD + 1e-9:
                continue
            cost = ya if side == "bid" else 1 - yb
            if cost < STEADY_MIN_PRICE - 1e-9 or cost > STEADY_MAX_PRICE + 1e-9:
                _log_once(ticker + ":px", f"[STEADY] {ticker} ({n['coin']}): qualifies at {n['p']*100:.0f}% but price "
                                          f"{cost*100:.0f}c is outside {STEADY_MIN_PRICE*100:.0f}-{STEADY_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            limit = min(STEADY_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[STEADY] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        cands.append((cost, ticker, n, side, limit, tl))
    # 2 Nov 15:11 CT (Matt): cheapest first -- the closer to 90c, the more each share pays.
    cands.sort(key=lambda c: c[0])
    for cost, ticker, n, side, limit, tl in cands:
        _attempts[ticker] = _attempts.get(ticker, 0) + 1
        print(f"[STEADY] ENTRY {ticker} ({n['coin']}): {n['side'].upper()} held all round (never flipped, never <=49%), "
              f"now {n['p']*100:.0f}% with {int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {STEADY_SHARES} "
              f"up to ${limit:.2f}")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * STEADY_SHARES)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            client.create_order(ticker=ticker, client_order_id=f"pulse-steady-{uuid.uuid4()}", side=side,
                                count=str(STEADY_SHARES), price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
        except Exception as e:
            print(f"[STEADY] {ticker}: entry order failed ({e}).")
            if "market_closed" in str(e) or "market_not_found" in str(e):
                excluded_tickers.add(ticker)
            continue
        filled, exposure = 0.0, 0.0
        for _ in range(4):
            try:
                filled, exposure = flip_star._held_count(client, ticker)
            except Exception:
                pass
            if filled != 0:
                break
            time.sleep(0.4)
        count = abs(filled)
        if count < 1e-9:
            print(f"[STEADY] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry once if it still qualifies.")
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                             "flip_star": True, "steady": True, "entry_win": n.get("p"), "entry_zs": n.get("zs")}
        _attempts[ticker] = STEADY_MAX_ATTEMPTS
        print(f"[STEADY] FILLED {ticker}: {count:g} {n['side'].upper()} @ ${entry_price:.2f}.")
