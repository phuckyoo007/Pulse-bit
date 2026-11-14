"""
LATE78 -- REAL MONEY, 2 shares (13 Nov 19:31 CT, Matt): "anything 78c or more within the last 2 minutes, bid on it.
If it falls back 20% exit; otherwise ride it to the end, or until 99c and take the profit."
  * Last LATE78_MAX_SECONDS_LEFT seconds (2:00) down to LATE78_MIN_SECONDS_LEFT.
  * The board's pick, priced LATE78_MIN_PRICE..LATE78_MAX_PRICE (78-98c). Every coin except DOGE. Own slot per coin.
  * EXIT (bot.py): stop at 80% of what we paid (fell back 20%); take the profit at 99c; otherwise held to the end.
    A fill far under 78c sells at once.
"""
import time
import uuid

import flip_star
import strategy_guard
import kalshi_ws

LATE78_ENABLED = True   # 13 Nov 19:31 CT (Matt): live
LATE78_SHARES = 2
LATE78_DOLLARS = 0
LATE78_COINS = None
LATE78_SKIP_COINS = {"DOGE"}
LATE78_MAX_SECONDS_LEFT = 120
LATE78_MIN_SECONDS_LEFT = 8
LATE78_MIN_PRICE = 0.78
LATE78_MAX_PRICE = 0.98
LATE78_STOP_FRACTION = 0.80   # fell back 20% -> out
LATE78_TAKE_PROFIT_PRICE = 0.99
LATE78_BOARD_TOLERANCE = -0.02   # 13 Nov 23:46 CT (loss review): board within 2 pts of the price, like Ride 82 -- 11 PM losses had board 5-25 pts UNDER the price (BNB 56% vs 81c, SOL 74% vs 80c, BTC 85% vs 90c)
LATE78_MIN_EDGE = 0.0
LATE78_MIN_EDGE_UNDER90 = 0.0

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
    if not LATE78_ENABLED or flip_star.in_pause():
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
        if "15M" not in ticker.split("-")[0].upper():   # 13 Nov: every coin -- no coin filter for LATE78
            continue   # 7 Nov 14:10 CT (Matt): Bitcoin + the coin with the 2nd-biggest volume
        tl = n.get("tl")
        if tl is None or tl > LATE78_MAX_SECONDS_LEFT or tl < LATE78_MIN_SECONDS_LEFT:
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        if not strategy_guard.can_enter(ticker, state, "LATE78-" + str(n.get("coin")), own_slot=True):
            continue   # 12 Nov: one coin per round + cooldown after a loss
        if LATE78_COINS and n.get("coin") not in LATE78_COINS:
            continue
        if n.get("coin") in LATE78_SKIP_COINS:
            continue
        side_name = n.get("side")
        if side_name not in ("yes", "no"):
            continue
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
                _log_once(ticker + ":lq", f"[LATE78] {ticker}: no live price yet -- waiting for the stream before buying.")
                continue
            _lc = _lq[1] if side == "bid" else 1 - _lq[0]
            if _lc < cost - 0.02 - 1e-9:
                _log_once(ticker + ":lqd", f"[LATE78] {ticker}: live price {_lc*100:.0f}c vs lookup {cost*100:.0f}c -- price is "
                                           f"FALLING fast, not buying this pass.")
                continue
            if _lc > cost + 1e-9:
                cost = _lc   # 13 Nov 16:28 CT (Matt: "don't skip those"): price rising our way -> use the live price
            if cost < LATE78_MIN_PRICE - 1e-9 or cost > LATE78_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[LATE78] {ticker}: board pick {side_name.upper()} but price {cost*100:.0f}c is "
                                  f"outside {LATE78_MIN_PRICE*100:.0f}-{LATE78_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            _edge = LATE78_BOARD_TOLERANCE
            if (n.get("p") or 0) < cost + _edge - 1e-9:
                _log_once(ticker + ":bp", f"[LATE78] {ticker}: board {(n.get('p') or 0)*100:.0f}% is below the {cost*100:.0f}c "
                                          f"price by more than 2 pts -- skipping.")
                continue
            limit = min(LATE78_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[LATE78] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        strategy_guard.note_entry(ticker, own_slot="LATE78-" + str(n.get("coin")))
        qty = max(0.01, int(LATE78_DOLLARS / limit * 100) / 100) if LATE78_DOLLARS else float(LATE78_SHARES)
        print(f"[LATE78] ENTRY {ticker} ({n.get('coin')}): board pick {side_name.upper()} ({(n.get('p') or 0)*100:.0f}%), "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {qty:.2f} share up to ${limit:.2f} "
              f"(about ${qty*limit:.2f})")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * qty)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            coid = f"pulse-surge-late78-{uuid.uuid4()}"
            try:
                client.create_order(ticker=ticker, client_order_id=coid, side=side, count=(str(int(qty)) if abs(qty - round(qty)) < 1e-9 else f"{qty:.2f}"),
                                    price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
            except Exception as e1:
                if "insufficient_balance" in str(e1) or "market_" in str(e1):
                    raise
                print(f"[LATE78] {ticker}: fractional 'count' refused ({e1}) -- retrying as count_fp.")
                body = {"ticker": ticker, "client_order_id": f"pulse-surge-late78-{uuid.uuid4()}", "side": side,
                        "count_fp": f"{qty:.2f}", "price": f"{yes_price:.2f}", "time_in_force": "immediate_or_cancel",
                        "self_trade_prevention_type": "taker_at_cross"}
                print(f"  [RAW ORDER BODY]: {body}")
                client._request("POST", "/portfolio/events/orders", json_body=body)
        except Exception as e:
            print(f"[LATE78] {ticker}: entry order failed ({e}).")
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
            print(f"[LATE78] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry while it qualifies.")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                             "flip_star": False, "surge": True, "late78": True,
                             "bad_fill": entry_price < LATE78_MIN_PRICE - 0.005}
        if entry_price < LATE78_MIN_PRICE - 0.005:
            print(f"[LATE78] {ticker}: filled at ${entry_price:.2f}, under the {LATE78_MIN_PRICE*100:.0f}c minimum -- "
                  f"price is falling fast, selling right away (11 Nov, Matt).")
        print(f"[LATE78] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- 80% stop, sells at 99c, else held to the end.")
