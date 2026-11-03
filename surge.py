"""
SURGE strategy (3 Nov 10:40 CT, Matt) -- momentum rides, using the edge board's MOMENTUM meter (-100..+100).

  * Any time in a 15-minute round, any win %: when a coin's momentum meter reads 100 (full strength), buy that
    direction -- +100 (rising) buys UP, -100 (falling) buys DOWN. 5 shares, one bet per coin per round.
  * Sell the second the meter drops below 90 in our direction (bot.py exit loop, "surge" positions).
  * Never pays more than 97c (a 98-99c share can't pay for itself).
Momentum = flip_star's mirror of the board meter (same formula, smoothed the same way).
"""
import time
import uuid

import flip_star

SURGE_ENABLED = True
SURGE_SHARES = 1             # 3 Nov 11:36 CT (Matt): 1 share per bet while we see if it works
SURGE_MAX_PER_ROUND = 5      # ...and at most 5 bets (5 different coins) per 15-minute round
SURGE_ENTRY = 0.845          # 3 Nov 11:21 CT (Matt): buy from 85 up to 100 on the meter, while it is still climbing
SURGE_RISE_SECONDS = 3       # "climbing" = higher than ~3 seconds ago
SURGE_EXIT = 0.245           # 3 Nov 11:19 CT (Matt): sell when the meter drops under 25 our way (was 90)
SURGE_MIN_PRICE = 0.48       # 3 Nov 10:43 CT (Matt): only buy when our side's price is $.48-$.85
SURGE_MAX_PRICE = 0.85
SURGE_MIN_SECONDS_LEFT = 20

_done = {}      # ticker -> time we bet it (one bet per coin per round)
_tries = {}     # ticker -> entry attempts (max 3)
_mhist = {}     # ticker -> [(time, momentum)] for the rising check
_note = {}


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


def momentum_for(ticker: str, side: str):
    """Meter reading for OUR side (+1 = full strength our way), or None without fresh data."""
    ag = flip_star.momentum_against(ticker, side)
    return None if ag is None else -ag


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    if not SURGE_ENABLED:
        return
    b = flip_star.board()
    if not b:
        return
    now = time.time()
    for t in [t for t, at in _done.items() if now - at > 1800]:
        _done.pop(t, None)
    for ticker, n in b.items():
        if "15M" not in ticker.split("-")[0].upper():
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        tl = n.get("tl")
        if tl is None or tl < SURGE_MIN_SECONDS_LEFT:
            continue
        _rk = ticker.split("-")[1] if "-" in ticker else ""
        if sum(1 for t in _done if t.split("-")[1:2] == [_rk]) >= SURGE_MAX_PER_ROUND:
            continue
        up = momentum_for(ticker, "yes")
        if up is None:
            continue
        hist = _mhist.setdefault(ticker, [])
        hist.append((now, up))
        while hist and now - hist[0][0] > 30:
            hist.pop(0)
        ref = None
        for t0, v0 in hist:
            if now - t0 >= SURGE_RISE_SECONDS:
                ref = v0
        if abs(up) < SURGE_ENTRY:
            continue
        # 3 Nov 11:21 CT (Matt): only while it is ESCALATING -- the meter must be higher (our way) than a few seconds ago.
        if ref is None or abs(up) <= abs(ref) + 1e-6 or (ref * up) < 0:
            continue
        side_name = "yes" if up > 0 else "no"
        side = "bid" if side_name == "yes" else "ask"
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
            if cost < SURGE_MIN_PRICE - 1e-9 or cost > SURGE_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[SURGE] {ticker} ({n['coin']}): momentum 100 {'UP' if up > 0 else 'DOWN'} but price "
                                  f"{cost*100:.0f}c is outside {SURGE_MIN_PRICE*100:.0f}-{SURGE_MAX_PRICE*100:.0f}c -- skipping.")
                continue
            limit = min(SURGE_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[SURGE] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        print(f"[SURGE] ENTRY {ticker} ({n['coin']}): momentum meter at {abs(up)*100:.0f} and climbing {'UP' if up > 0 else 'DOWN'}, "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {SURGE_SHARES} {side_name.upper()} up to ${limit:.2f}")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * SURGE_SHARES)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            client.create_order(ticker=ticker, client_order_id=f"pulse-surge-{uuid.uuid4()}", side=side,
                                count=str(SURGE_SHARES), price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
        except Exception as e:
            print(f"[SURGE] {ticker}: entry order failed ({e}).")
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
            print(f"[SURGE] {ticker}: nothing filled at ${limit:.2f} (price moved).")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)   # allow another try (max 3) while the meter is still at 100
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                             "flip_star": True, "surge": True}
        print(f"[SURGE] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- sells when momentum drops under {SURGE_EXIT*100:.0f}.")
