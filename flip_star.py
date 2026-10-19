"""
FLIP-STAR strategy -- runs ALONGSIDE Pulse's other entry rules.

Uses the exact same numbers the Crypto Edge Board dashboard shows (it reads the board's /api/state), so what the
bot does matches what you see on the top coin boxes:

  ENTRY (13 shares of the favored side) when, with 8 minutes or less left in the 15-minute window:
      the coin has the RED STAR, or its "To flip" line is GREEN (2+ sigma) or YELLOW 1 / bid zone (1.5-2 sigma).
  HOLD through Yellow 1/2/3 (0.5-2 sigma); Yellow 3 (0.5-1) is the dashboard's "get ready to sell" warning.
  EXIT as soon as "To flip" turns RED (under 0.5 sigma).  [zones moved down 18 Oct: red was under 1]

If the board can't be reached (or its data is stale) the bot opens nothing new, and any flip-star position it holds
falls back to Pulse's normal stop-loss until fresh numbers come back. Pulse's hard backstop (72%) still applies.
"""
import os
import time
import uuid

import requests

FLIP_ENABLED = True
FLIP_SHARES = 10                    # shares per flip-star entry
FLIP_MAX_SECONDS_LEFT = 360        # only the last 6 minutes of the window (was 8)
FLIP_MIN_SECONDS_LEFT = 30         # too close to the bell to get a clean fill
FLIP_NONSTAR_MIN_SECONDS_LEFT = 30   # green / Yellow 1: allowed right up to the last 30s again (was blocked under 2.5 min)
FLIP_ENTRY_SIGMA = 0.5            # 19 Oct 18:08: buy from Yellow 3 up (0.5+ sigma); sells the moment it turns red (<0.5)
FLIP_EXIT_SIGMA = 0.5              # "To flip" red on the dashboard (under 0.5) -> pull out (was 1.0)
FLIP_EXIT_CONFIRM_SECONDS = 0      # 19 Oct: sell the moment To flip turns red (was 10s wait)
FLIP_EXIT_MAX_WIN = 1.01           # 19 Oct: no win-% check on red -- red alone sells (was 0.90)
FLIP_EXIT_PANIC_WIN = 0.60         # (was 0.75) if win % falls under 60% the coin is really turning -- sell at once, no waiting
FLIP_STOP_CONFIRM_SECONDS = 5      # (18 Oct 19:21: was 10) the 80% / 20c price stop must also hold this long (and win % be under 90%) -- thin books blip
FLIP_USE_80_STOP = False            # 19 Oct: 80% stop off for flip bets (3 of 4 stop-outs today were winners); 70% hard stop, 50% crash stop and To-flip-red exit stay on
FLIP_MAX_SPREAD = 0.05             # skip thin, jumpy books
FLIP_MAX_PRICE = 0.98              # 19 Oct 18:08: bid 93-98c (red star)
FLIP_NONSTAR_MAX_PRICE = 0.98      # 19 Oct 18:08: bid 93-98c (Y3 and up)
FLIP_NONSTAR_MIN_PRICE = 0.93      # 19 Oct 18:08: 93c or higher
FLIP_NONSTAR_MIN_WIN = 0.0         # 19 Oct 18:08: no win-% requirement for non-star entries (price range 93-98c is the filter)
FLIP_STAR_MIN_PRICE = 0.93         # 19 Oct 18:08: red star 93c or higher; breaker can only raise it
FLIP_STAR_REST_PRICE = 0.96        # 18 Oct: red star priced above this -> post a resting bid HERE and wait, instead of paying 97-99c
FLIP_RESTING_BIDS = False          # 18 Oct 19:15 CT: OFF. Resting 96c bids only filled when the price was falling into them
                                   # (HYPE -0.08, BTC -10.90, ETH -3.30 within seconds of filling). Buy at the live price only.
# FALLING-KNIFE GUARD (18 Oct): never buy while the coin or our side's price is moving against us.
FLIP_MAX_SIDE_DROP_1M = 0.02       # skip if our side's Kalshi price fell 2c+ in the last minute
FLIP_MAX_SPOT_AGAINST_1M = 0.05    # 19 Oct 18:07: loosened from 0.02% -- tiny wiggles were blocking most entries; still blocks real moves (0.1%+)
FLIP_RED_STAR_ONLY = False         # 18 Oct: green entries back on (red star + green at 95%+)

# FLOOR CIRCUIT BREAKER (18 Oct): the red-star floor is on trial at 92c. If any flip-star bet bought under 93c
# ends in a loss (early exit, emergency backstop or settlement), the floor goes straight back to 93c and stays
# there. The trip is saved to a small file so a restart keeps it (a fresh deploy starts at 92c again).


def _order_fee(count, price):
    """Kalshi taker fee for one order, in dollars: ceil(0.07 * C * p * (1-p)) cents."""
    import math
    return math.ceil(0.07 * count * price * (1 - price) * 100 - 1e-9) / 100


FLIP_STAR_SAFE_MIN_PRICE = 0.93
_BREAKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flip_floor_breaker.json")
try:
    if os.path.exists(_BREAKER_FILE):
        FLIP_STAR_MIN_PRICE = max(FLIP_STAR_MIN_PRICE, FLIP_STAR_SAFE_MIN_PRICE)
        print(f"[FLIP] floor circuit breaker already tripped earlier -- red-star floor stays at {FLIP_STAR_MIN_PRICE*100:.0f}c.")
except Exception:
    pass


# LOSS KILL SWITCH (18 Oct): after 3 losing flip-star bets, stop opening new bets until Pulse is restarted
# (restart the pulse service in Railway). Open bets keep their stops; only new buying stops.
FLIP_MAX_LOSSES_BEFORE_HALT = 3
_loss_count = 0
FLIP_PAUSED = False                # set to True to pause new bets (open bets keep their stops)
FLIP_HALTED = FLIP_PAUSED
_halt_note_at = 0.0


def record_result(position, pnl, how=""):
    """Called by bot.py whenever a position closes. Counts losses for the kill switch and trips the floor breaker
    on a losing flip-star bet bought under 93c."""
    global FLIP_STAR_MIN_PRICE, _loss_count, FLIP_HALTED
    try:
        if not position or not position.get("flip_star"):
            return
        entry = float(position.get("entry_price") or 0)
        if pnl < -1e-9:
            _loss_count += 1
            print(f"[FLIP] loss {_loss_count}/{FLIP_MAX_LOSSES_BEFORE_HALT} since restart: ${-pnl:.2f} ({how}).")
            if _loss_count >= FLIP_MAX_LOSSES_BEFORE_HALT and not FLIP_HALTED:
                FLIP_HALTED = True
                print(f"[FLIP] KILL SWITCH: {_loss_count} losing bets since the last restart -- NO NEW BETS until Pulse "
                      f"is restarted in Railway. Open bets are still protected by their stops.")
        if pnl < -1e-9 and entry < FLIP_STAR_SAFE_MIN_PRICE - 0.005 and FLIP_STAR_MIN_PRICE < FLIP_STAR_SAFE_MIN_PRICE:
            FLIP_STAR_MIN_PRICE = FLIP_STAR_SAFE_MIN_PRICE
            print(f"[FLIP] FLOOR CIRCUIT BREAKER TRIPPED: a bet bought at ${entry:.2f} lost ${-pnl:.2f} ({how}) -- "
                  f"red-star floor back to {FLIP_STAR_SAFE_MIN_PRICE*100:.0f}c.")
            try:
                import json as _json
                with open(_BREAKER_FILE, "w") as f:
                    _json.dump({"tripped_at": time.time(), "entry": entry, "pnl": pnl, "how": how}, f)
            except Exception as e:
                print(f"[FLIP] couldn't save the breaker state ({e}) -- floor is 93c until the next restart.")
    except Exception as e:
        print(f"[FLIP] record_result error (non-fatal): {e}")
FLIP_SLIPPAGE = 0.01               # limit price = ask + 1c (capped at FLIP_MAX_PRICE); order is immediate-or-cancel
FLIP_MAX_ATTEMPTS_PER_TICKER = 3
FLIP_STALE_SECONDS = 15            # board data older than this is not trusted
EDGE_BOARD_URL = os.getenv("EDGE_BOARD_URL", "https://edge-board-production.up.railway.app/api/state")

_board = {"data": {}, "fetched": 0.0, "ok_at": 0.0}
_attempts = {}
_red_since = {}
_sold_red = set()   # tickers already sold on red this session -- never re-buy the same round (no buy/sell churn)


def _side_numbers(m: dict, age: float, coins: dict = None):
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
            "title": m.get("title", m.get("ticker")),
            "chg_1m": ((coins or {}).get(m.get("coin")) or {}).get("chg_1m"),
            "chg_5m": ((coins or {}).get(m.get("coin")) or {}).get("chg_5m"),
            "mid_chg_1m": m.get("mid_chg_1m")}


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
            n = _side_numbers(m, age, st.get("coins"))
            if n and n["ticker"]:
                out[n["ticker"]] = n
        _board["data"], _board["ok_at"] = out, now
    except Exception as e:
        print(f"[FLIP] couldn't read the dashboard ({e}) -- no new flip-star entries until it's back.")
    return _board["data"] if now - _board["ok_at"] <= FLIP_STALE_SECONDS else {}


_knife_note = {}


def _falling_against(n: dict) -> bool:
    """True when the coin or our side's price is moving against us right now -- don't buy into a drop."""
    sgn = 1 if n["side"] == "yes" else -1
    why = None
    mc = n.get("mid_chg_1m")
    if mc is not None and mc * sgn <= -FLIP_MAX_SIDE_DROP_1M + 1e-9:
        why = f"our side's price fell {abs(mc)*100:.0f}c in the last minute"
    c1 = n.get("chg_1m")
    if why is None and c1 is not None and c1 * sgn <= -FLIP_MAX_SPOT_AGAINST_1M + 1e-9:
        why = f"{n.get('coin')} moved {abs(c1):.3f}% against us in the last minute"
    if why and time.time() - _knife_note.get(n.get("ticker"), 0) > 30:
        _knife_note[n.get("ticker")] = time.time()
        print(f"[FLIP] {n.get('ticker')}: skipping -- {why} (falling-knife guard).")
    return why is not None


def entry_signal(n: dict) -> bool:
    if n["zs"] is None or n["tl"] is None or not (FLIP_MIN_SECONDS_LEFT <= n["tl"] <= FLIP_MAX_SECONDS_LEFT):
        return False
    if n["lock"]:
        return True
    if FLIP_RED_STAR_ONLY:
        return False   # red stars only -- no green / yellow entries
    return (n["zs"] >= FLIP_ENTRY_SIGMA and n["p"] >= FLIP_NONSTAR_MIN_WIN - 1e-9
            and n["tl"] >= FLIP_NONSTAR_MIN_SECONDS_LEFT)


def win_now(ticker: str):
    """Model win chance for the favored side right now, or None if the board has no fresh numbers."""
    n = board().get(ticker)
    return None if not n else n["p"]


def exit_decision(ticker: str):
    """(should_exit, reason) for a flip-star position, or None if the board has no fresh numbers for it
    (caller then falls back to the normal stop-loss)."""
    n = board().get(ticker)
    if not n or n["zs"] is None:
        return None
    # Lesson from ZEC 15 Oct 22:26: To-flip dropped 1.7 -> 0.9 sigma in one minute on a small wiggle while the model
    # (and the market, at 98c) still gave our side 98%. One red reading sold a winning position for -$0.46.
    # Now a red reading only counts if it lasts and the win % has actually dropped; a real collapse still exits at once.
    now = time.time()
    if n["p"] < FLIP_EXIT_PANIC_WIN:
        _red_since.pop(ticker, None)
        _sold_red.add(ticker)
        return True, f"flip_collapse (win % fell to {n['p']*100:.0f}%, To flip {n['zs']:.1f} sigma)"
    if n["zs"] < FLIP_EXIT_SIGMA:
        since = _red_since.setdefault(ticker, now)
        held = now - since
        if held >= FLIP_EXIT_CONFIRM_SECONDS and n["p"] < FLIP_EXIT_MAX_WIN:
            _red_since.pop(ticker, None)
            _sold_red.add(ticker)
            return True, (f"flip_red (To flip under {FLIP_EXIT_SIGMA:g} sigma for {held:.0f}s, now {n['zs']:.1f}; "
                          f"win {n['p']*100:.0f}%)")
        if held < 1.0:
            print(f"[FLIP] {ticker}: To flip red ({n['zs']:.1f} sigma, win {n['p']*100:.0f}%) -- watching; sells if it stays red "
                  f"{FLIP_EXIT_CONFIRM_SECONDS}s with win under {FLIP_EXIT_MAX_WIN*100:.0f}%, or at once if win drops under "
                  f"{FLIP_EXIT_PANIC_WIN*100:.0f}%.")
        return False, ""
    if ticker in _red_since:
        print(f"[FLIP] {ticker}: back out of red ({n['zs']:.1f} sigma) -- holding.")
        _red_since.pop(ticker, None)
    return False, ""


def _held_count(client, ticker):
    resp = client.get_positions()
    for p in resp.get("market_positions", resp.get("positions", [])):
        if p.get("ticker") == ticker:
            return float(p.get("position_fp", p.get("position", 0)) or 0), float(p.get("market_exposure_dollars", 0) or 0)
    return 0.0, 0.0


_resting = {}   # ticker -> resting red-star bid waiting at FLIP_STAR_REST_PRICE


def pending_tickers() -> set:
    """Tickers with a resting flip-star bid -- the manual-position adopter must leave these alone."""
    return set(_resting)


def _find_order_id(client, ticker, client_order_id, resp):
    for src in (resp or {}, (resp or {}).get("order") or {}):
        if isinstance(src, dict) and src.get("order_id"):
            return src["order_id"]
    try:
        orders = client._request("GET", "/portfolio/orders", params={"ticker": ticker, "status": "resting"}).get("orders", [])
        for o in orders:
            if o.get("client_order_id") == client_order_id:
                return o.get("order_id")
    except Exception as e:
        print(f"[FLIP] {ticker}: couldn't look up the resting order id ({e}).")
    return None


def _record_fill(client, state, state_lock, log_trade, ticker, n, side, count, entry_price, how):
    with state_lock:
        state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                         "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                         "flip_star": True, "force_exit": False}
    print(f"[FLIP] FILLED {ticker}: {count:g} {n['side'].upper()} @ ${entry_price:.2f} ({how}) -- holding until To flip turns red.")
    try:
        from types import SimpleNamespace
        log_trade(SimpleNamespace(ticker=ticker, title=n["title"], coin=n["coin"], seconds_remaining=n["tl"],
                                  model_prob=n["p"], edge_pct=None),
                  side, entry_price, count, False, entry_reason="flip_star_red")
    except Exception as e:
        print(f"[FLIP] trade log failed (non-fatal): {e}")


def _check_resting(client, state, state_lock, log_trade, b):
    """Each pass: a resting red-star bid that filled becomes a normal flip-star position; one whose red star is
    gone (or the window is nearly over) is cancelled."""
    for ticker, r in list(_resting.items()):
        n = b.get(ticker) or r["n"]
        still_good = bool(b.get(ticker)) and n["lock"] and entry_signal(n)
        try:
            held, exposure = _held_count(client, ticker)
        except Exception as e:
            print(f"[FLIP] {ticker}: couldn't check the resting bid ({e}).")
            continue
        if held == 0 and still_good:
            continue   # still waiting at the resting price
        # Filled (some or all), or the red star is gone: cancel whatever is left, then take what we got.
        if r.get("order_id"):
            try:
                client.cancel_order(r["order_id"])
            except Exception as e:
                if "not_found" not in str(e) and "404" not in str(e):
                    print(f"[FLIP] {ticker}: cancel of resting bid failed ({e}) -- will try again next pass.")
                    continue
        try:
            held, exposure = _held_count(client, ticker)
        except Exception:
            pass
        _resting.pop(ticker, None)
        count = abs(held)
        if count >= 1e-9:
            entry_price = exposure / count if exposure > 0 else FLIP_STAR_REST_PRICE
            _record_fill(client, state, state_lock, log_trade, ticker, r["n"], r["side"], count, entry_price,
                         f"resting {FLIP_STAR_REST_PRICE*100:.0f}c bid filled")
        else:
            print(f"[FLIP] {ticker}: red star gone or window ending -- resting {FLIP_STAR_REST_PRICE*100:.0f}c bid cancelled, nothing filled.")


def run_entries(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, log_trade, ensure_shard_funds):
    """Open 4-share flip-star positions where the dashboard says so. Called once per scan loop."""
    global _halt_note_at
    if not FLIP_ENABLED:
        return
    if FLIP_HALTED:
        if _resting:
            _check_resting(client, state, state_lock, log_trade, {})   # halted: cancel any resting bids
        if time.time() - _halt_note_at > 300:
            _halt_note_at = time.time()
            print("[FLIP] PAUSED by request -- no new bets (FLIP_PAUSED=True in flip_star.py)." if FLIP_PAUSED and _loss_count < FLIP_MAX_LOSSES_BEFORE_HALT
                  else f"[FLIP] halted after {_loss_count} losses -- restart Pulse in Railway to resume betting.")
        return
    b = board()
    if _resting:
        _check_resting(client, state, state_lock, log_trade, b)
    if not b:
        return
    for ticker, n in b.items():
        if not entry_signal(n):
            continue
        if ticker in state or ticker in excluded_tickers or ticker in _resting or ticker in _sold_red:
            continue
        if _attempts.get(ticker, 0) >= FLIP_MAX_ATTEMPTS_PER_TICKER:
            continue
        if _falling_against(n):
            continue
        side = "bid" if n["side"] == "yes" else "ask"
        try:
            held, _ = _held_count(client, ticker)
            if held != 0:
                continue   # already holding this market (main strategy or a manual bet) -- don't double up
            # Live quote straight from Kalshi (the board can lag a second or two).
            mk = client.get_market(ticker).get("market", {})
            ya, yb = mk.get("yes_ask_dollars"), mk.get("yes_bid_dollars")
            if ya is None or yb is None:
                continue
            ya, yb = float(ya), float(yb)
            if ya - yb > FLIP_MAX_SPREAD + 1e-9:
                continue
            cost = ya if side == "bid" else 1 - yb                       # what one share of our side costs now
            max_px = FLIP_MAX_PRICE if n["lock"] else FLIP_NONSTAR_MAX_PRICE
            if cost <= 0 or cost > max_px + 1e-9:
                continue
            if not n["lock"] and cost < FLIP_NONSTAR_MIN_PRICE - 1e-9:
                continue   # no red star: only buy at 96c or higher
            if n["lock"] and cost < FLIP_STAR_MIN_PRICE - 1e-9:
                continue   # red star: only buy at the current floor or higher
            limit_side_price = min(max_px, round(cost + FLIP_SLIPPAGE, 2))
            rest = FLIP_RESTING_BIDS and bool(n["lock"]) and cost > FLIP_STAR_REST_PRICE + 1e-9
            if not rest and cost > FLIP_STAR_REST_PRICE + 1e-9 and not FLIP_RESTING_BIDS:
                pass   # resting bids off: buy at the live price (up to the cap) like before
            if rest:
                limit_side_price = FLIP_STAR_REST_PRICE
        except Exception as e:
            print(f"[FLIP] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue

        if rest and not dry_run:
            coid = f"pulse-flip-{uuid.uuid4()}"
            print(f"[FLIP] REST {ticker} ({n['coin']}): RED STAR at {cost*100:.0f}c, win {n['p']*100:.0f}%, {int(n['tl'])}s left -- "
                  f"posting a {FLIP_SHARES}-share {n['side'].upper()} bid at ${FLIP_STAR_REST_PRICE:.2f} and waiting")
            try:
                ensure_shard_funds(client, ticker, FLIP_STAR_REST_PRICE * FLIP_SHARES)
                yes_price = FLIP_STAR_REST_PRICE if side == "bid" else round(1 - FLIP_STAR_REST_PRICE, 2)
                resp = client.create_order(ticker=ticker, client_order_id=coid, side=side, count=str(FLIP_SHARES),
                                           price=f"{yes_price:.2f}", time_in_force="good_till_canceled")
            except Exception as e:
                print(f"[FLIP] {ticker}: resting bid failed ({e}).")
                if "market_closed" in str(e) or "market_not_found" in str(e):
                    excluded_tickers.add(ticker)
                continue
            _attempts[ticker] = _attempts.get(ticker, 0) + 1
            _resting[ticker] = {"order_id": _find_order_id(client, ticker, coid, resp), "side": side, "n": n,
                                "placed": time.time()}
            continue

        _attempts[ticker] = _attempts.get(ticker, 0) + 1
        why = "RED STAR" if n["lock"] else (f"To flip GREEN {n['zs']:.1f} sigma" if n["zs"] >= 2 else f"To flip YELLOW 1 (bid zone) {n['zs']:.1f} sigma")
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
        # Lesson from BNB 15 Oct 20:12 CT: the quote passed the 96c floor, but by the time the order hit the book the
        # price had dropped and the order filled at 91c (a buy limit is only a MAXIMUM -- it happily fills cheaper).
        # A cheaper fill means the market just lost confidence, so anything under the floor is sold straight away.
        floor = FLIP_STAR_MIN_PRICE if n["lock"] else FLIP_NONSTAR_MIN_PRICE
        below_floor = entry_price < floor - 0.005
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                             "flip_star": True, "force_exit": below_floor}
        if below_floor:
            print(f"[FLIP] FILLED {ticker}: {count:g} {n['side'].upper()} @ ${entry_price:.2f} -- UNDER the {floor*100:.0f}c floor "
                  f"(price dropped between the check and the fill) -- selling right away.")
        else:
            print(f"[FLIP] FILLED {ticker}: {count:g} {n['side'].upper()} @ ${entry_price:.2f} -- holding until To flip turns red.")
        try:
            from types import SimpleNamespace
            log_trade(SimpleNamespace(ticker=ticker, title=n["title"], coin=n["coin"], seconds_remaining=n["tl"],
                                      model_prob=n["p"], edge_pct=None),
                      side, entry_price, count, False, entry_reason="flip_star_red" if n["lock"] else ("flip_star_green" if n["zs"] >= 2 else "flip_star_y1"))
        except Exception as e:
            print(f"[FLIP] trade log failed (non-fatal): {e}")
