"""
MOMENTUM RIDE -- REAL MONEY, 2 shares (13 Nov 18:54 CT, Matt): "once something hits 90% momentum, buy in; ride it as
long as you can over the 15-minute window until momentum falls below 50%."
  * Momentum = the edge board's MOMENTUM meter (-1..+1, from the coin's 1- and 5-minute spot moves).
  * ENTRY: momentum toward a side >= MOMRIDE_BUY_MOMENTUM, that side priced MOMRIDE_MIN_PRICE..MOMRIDE_MAX_PRICE,
    at least MOMRIDE_MIN_SECONDS_LEFT left. One ride per coin per round (its own slot). Every coin except DOGE.
  * EXIT (bot.py): momentum toward our side drops below MOMRIDE_SELL_MOMENTUM -> sell; otherwise held to the end.
    A fill far under the price we saw sells at once (price crashing as it filled).
Paper twin: hour_study MOM90 (and MOM80 for comparison).
"""
import time
import uuid

import flip_star
import strategy_guard
import kalshi_ws

MOMRIDE_ENABLED = True   # 13 Nov 18:54 CT (Matt): live, 2 shares
MOMRIDE_SHARES = 2
MOMRIDE_DOLLARS = 0
MOMRIDE_COINS = None
MOMRIDE_SKIP_COINS = {"DOGE"}
MOMRIDE_MAX_SECONDS_LEFT = 900
MOMRIDE_MIN_SECONDS_LEFT = 60
MOMRIDE_MIN_PRICE = 0.10
MOMRIDE_MAX_PRICE = 0.90
MOMRIDE_BUY_MOMENTUM = 0.90
MOMRIDE_SELL_MOMENTUM = 0.50
MOMRIDE_TAKE_PROFIT = 0.15   # 13 Nov 19:06 CT (Matt): sell once it is up 15c a share (was 20c)
MOMRIDE_BOARD_TOLERANCE = -1.0   # no board-vs-price rule for momentum rides
MOMRIDE_MIN_EDGE = 0.0
MOMRIDE_MIN_EDGE_UNDER90 = 0.0

_done = {}   # ticker -> time (one bet per round)
_tries = {}
_note = {}


def record(position, pnl) -> None:
    return


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    if not MOMRIDE_ENABLED or flip_star.in_pause():
        return
    b = flip_star.board()
    if not b:
        return
    now = time.time()
    try:
        kalshi_ws.watch_extra([t for t, x in b.items() if "15M" in t.split("-")[0].upper()])
    except Exception:
        pass
    for t in [t for t, at in _done.items() if now - at > 1800]:
        _done.pop(t, None)
        _tries.pop(t, None)
    for ticker, n in flip_star.by_priority(b):
        if "15M" not in ticker.split("-")[0].upper():   # 13 Nov: every coin -- no coin filter for MOMRIDE
            continue   # 7 Nov 14:10 CT (Matt): Bitcoin + the coin with the 2nd-biggest volume
        tl = n.get("tl")
        if tl is None or tl > MOMRIDE_MAX_SECONDS_LEFT or tl < MOMRIDE_MIN_SECONDS_LEFT:
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        if not strategy_guard.can_enter(ticker, state, "MOMRIDE-" + str(n.get("coin")), own_slot=True):
            continue   # 12 Nov: one coin per round + cooldown after a loss
        if MOMRIDE_COINS and n.get("coin") not in MOMRIDE_COINS:
            continue
        if n.get("coin") in MOMRIDE_SKIP_COINS:
            continue
        side_name = None
        for _s in ("yes", "no"):
            _ma = flip_star.momentum_against(ticker, _s)
            if _ma is not None and -_ma >= MOMRIDE_BUY_MOMENTUM - 1e-9:
                side_name = _s
        if side_name is None:
            continue   # momentum under 90% either way
        side = "bid" if side_name == "yes" else "ask"
        try:
            held, _ = flip_star._held_count(client, ticker)
            if held != 0:
                continue
            mk = client.get_market(ticker).get("market", {})
            ya, yb = float(mk.get("yes_ask_dollars")), float(mk.get("yes_bid_dollars"))
            if ya - yb > flip_star.FLIP_MAX_SPREAD + 1e-9:
                continue
            cost = ya if side == "bid" else 1 - yb
            # 11 Nov 02:45 CT (Matt): the REST quote can lag a fast-falling price (fills at 60-70c on an 80c+ check).
            # Require the live stream's price too, and skip this pass if the two disagree by more than 2c.
            _lq = kalshi_ws.quote(ticker)
            if _lq is None:
                _log_once(ticker + ":lq", f"[MOMRIDE] {ticker}: no live price yet -- waiting for the stream before buying.")
                continue
            _lc = _lq[1] if side == "bid" else 1 - _lq[0]
            if _lc < cost - 0.02 - 1e-9:
                _log_once(ticker + ":lqd", f"[MOMRIDE] {ticker}: live price {_lc*100:.0f}c vs lookup {cost*100:.0f}c -- price is "
                                           f"FALLING fast, not buying this pass.")
                continue
            if _lc > cost + 1e-9:
                cost = _lc   # 13 Nov 16:28 CT (Matt: "don't skip those"): price rising our way -> use the live price
            if cost < MOMRIDE_MIN_PRICE - 1e-9 or cost > MOMRIDE_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[MOMRIDE] {ticker}: momentum {side_name.upper()} but price {cost*100:.0f}c is "
                                  f"outside {MOMRIDE_MIN_PRICE*100:.0f}-{MOMRIDE_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            _edge = MOMRIDE_BOARD_TOLERANCE
            if (n.get("p") or 0) < cost + _edge - 1e-9:
                _log_once(ticker + ":bp", f"[MOMRIDE] {ticker}: board {(n.get('p') or 0)*100:.0f}% is below the {cost*100:.0f}c "
                                          f"price by more than 2 pts -- skipping.")
                continue
            limit = min(MOMRIDE_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[MOMRIDE] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        strategy_guard.note_entry(ticker, own_slot="MOMRIDE-" + str(n.get("coin")))
        qty = max(0.01, int(MOMRIDE_DOLLARS / limit * 100) / 100) if MOMRIDE_DOLLARS else float(MOMRIDE_SHARES)
        print(f"[MOMRIDE] ENTRY {ticker} ({n.get('coin')}): momentum {(-flip_star.momentum_against(ticker, side_name))*100:.0f}% toward {side_name.upper()}, "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {qty:.2f} share up to ${limit:.2f} "
              f"(about ${qty*limit:.2f})")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * qty)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            coid = f"pulse-surge-momride-{uuid.uuid4()}"
            try:
                client.create_order(ticker=ticker, client_order_id=coid, side=side, count=(str(int(qty)) if abs(qty - round(qty)) < 1e-9 else f"{qty:.2f}"),
                                    price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
            except Exception as e1:
                if "insufficient_balance" in str(e1) or "market_" in str(e1):
                    raise
                print(f"[MOMRIDE] {ticker}: fractional 'count' refused ({e1}) -- retrying as count_fp.")
                body = {"ticker": ticker, "client_order_id": f"pulse-surge-momride-{uuid.uuid4()}", "side": side,
                        "count_fp": f"{qty:.2f}", "price": f"{yes_price:.2f}", "time_in_force": "immediate_or_cancel",
                        "self_trade_prevention_type": "taker_at_cross"}
                print(f"  [RAW ORDER BODY]: {body}")
                client._request("POST", "/portfolio/events/orders", json_body=body)
        except Exception as e:
            print(f"[MOMRIDE] {ticker}: entry order failed ({e}).")
            if "market_closed" in str(e) or "market_not_found" in str(e):
                excluded_tickers.add(ticker)
            elif "insufficient_balance" not in str(e):
                _done.pop(ticker, None)
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
            print(f"[MOMRIDE] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry while it qualifies.")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                             "flip_star": False, "surge": True, "momride": True,
                             "bad_fill": entry_price < MOMRIDE_MIN_PRICE - 0.005}
        if entry_price < MOMRIDE_MIN_PRICE - 0.005:
            print(f"[MOMRIDE] {ticker}: filled at ${entry_price:.2f}, under the {MOMRIDE_MIN_PRICE*100:.0f}c minimum -- "
                  f"price is falling fast, selling right away (11 Nov, Matt).")
        print(f"[MOMRIDE] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- riding until momentum drops under 50%.")
