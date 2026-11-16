"""
LAST120 -- REAL MONEY, 3 shares (16 Nov 11:19-11:21 CT, Matt): "last 120 seconds, anything 86c and over on every coin,
it has to hold for four seconds, 85% stop loss, early exit at 99c, floor 86 ceiling 97 ... run it live ... three shares".
  * Last LAST120_MAX_SECONDS_LEFT seconds (2:00) down to LAST120_MIN_SECONDS_LEFT, every coin, own slot per coin.
  * The favourite side (the one priced over 50c), 86-97c, and its price has been 86c+ for LAST120_HOLD_SECS straight.
  * No sigma / board checks -- price only, the way Matt laid it out.
  * EXIT (bot.py): sell at 99c; stop at 85% of what we paid; otherwise held to the end.  A fill far under 86c sells at once.
"""
import time
import uuid

import flip_star
import strategy_guard
import kalshi_ws

LAST120_ENABLED = True   # 16 Nov 11:20 CT (Matt): "run it live"
LAST120_SHARES = 3       # 16 Nov 11:20 CT (Matt): "three shares"
LAST120_DOLLARS = 0
LAST120_MAX_SECONDS_LEFT = 120
LAST120_MIN_SECONDS_LEFT = 5
LAST120_MIN_PRICE = 0.86
LAST120_MAX_PRICE = 0.97
LAST120_HOLD_SECS = 4
LAST120_STOP_FRACTION = 0.76   # 16 Nov 11:30 CT (Matt): 76% stop for ALL strategies (was 85%)
LAST120_TAKE_PROFIT_PRICE = 0.99

_done = {}
_tries = {}
_note = {}
_px = {}   # ticker -> [(t, yes_ask, no_ask)] from the board / live stream


def record(position, pnl) -> None:
    return


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


def _held(ticker, side_name, now):
    """The favourite's ask has been LAST120_MIN_PRICE+ on every reading for LAST120_HOLD_SECS straight."""
    h = _px.get(ticker, [])
    win = [x for x in h if now - x[0] <= LAST120_HOLD_SECS + 0.5]
    if len(win) < 2 or now - win[0][0] < LAST120_HOLD_SECS - 0.5:
        return False
    return all((x[1] if side_name == "yes" else x[2]) >= LAST120_MIN_PRICE - 1e-9 for x in win)


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    b = flip_star.board()
    if not b:
        return
    now = time.time()
    for t_, x_ in b.items():
        if "15M" not in t_.split("-")[0].upper():
            continue
        ya_, na_ = x_.get("yes_ask"), x_.get("no_ask")
        try:
            lq = kalshi_ws.quote(t_)   # live stream (bid, ask) for YES if we have it
            if lq is not None:
                ya_, na_ = lq[1], 1 - lq[0]
        except Exception:
            pass
        if ya_ is None or na_ is None:
            continue
        h_ = _px.setdefault(t_, [])
        h_.append((now, float(ya_), float(na_)))
        while h_ and now - h_[0][0] > 30:
            h_.pop(0)
    for t_ in [t_ for t_ in _px if t_ not in b]:
        _px.pop(t_, None)
    if not LAST120_ENABLED or flip_star.in_pause():
        return
    try:
        kalshi_ws.watch_extra([t for t in b if "15M" in t.split("-")[0].upper()])
    except Exception:
        pass
    for t in [t for t, at in _done.items() if now - at > 1800]:
        _done.pop(t, None)
        _tries.pop(t, None)
    for ticker, n in flip_star.by_priority(b):
        if "15M" not in ticker.split("-")[0].upper():
            continue
        tl = n.get("tl")
        if tl is None or tl > LAST120_MAX_SECONDS_LEFT or tl < LAST120_MIN_SECONDS_LEFT:
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        if not strategy_guard.can_enter(ticker, state, "LAST120-" + str(n.get("coin")), own_slot=True):
            continue
        h = _px.get(ticker)
        if not h:
            continue
        side_name = "yes" if h[-1][1] >= h[-1][2] else "no"   # the favourite = the pricier side
        if not _held(ticker, side_name, now):
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
                _log_once(ticker + ":lq", f"[LAST120] {ticker}: no live price yet -- waiting for the stream before buying.")
                continue
            _lc = _lq[1] if side == "bid" else 1 - _lq[0]
            if _lc < cost - 0.02 - 1e-9:
                _log_once(ticker + ":lqd", f"[LAST120] {ticker}: live price {_lc*100:.0f}c vs lookup {cost*100:.0f}c -- price is "
                                           f"FALLING fast, not buying this pass.")
                continue
            if _lc > cost + 1e-9:
                cost = _lc   # 13 Nov 16:28 CT (Matt: "don't skip those"): price rising our way -> use the live price
            if cost < LAST120_MIN_PRICE - 1e-9 or cost > LAST120_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[LAST120] {ticker}: board pick {side_name.upper()} but price {cost*100:.0f}c is "
                                  f"outside {LAST120_MIN_PRICE*100:.0f}-{LAST120_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            limit = min(LAST120_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[LAST120] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        strategy_guard.note_entry(ticker, own_slot="LAST120-" + str(n.get("coin")))
        qty = max(0.01, int(LAST120_DOLLARS / limit * 100) / 100) if LAST120_DOLLARS else float(LAST120_SHARES)
        print(f"[LAST120] ENTRY {ticker} ({n.get('coin')}): board pick {side_name.upper()} ({(n.get('p') or 0)*100:.0f}%), "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {qty:.2f} share up to ${limit:.2f} "
              f"(about ${qty*limit:.2f})")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * qty)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            coid = f"pulse-surge-last120-{uuid.uuid4()}"
            try:
                client.create_order(ticker=ticker, client_order_id=coid, side=side, count=(str(int(qty)) if abs(qty - round(qty)) < 1e-9 else f"{qty:.2f}"),
                                    price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
            except Exception as e1:
                if "insufficient_balance" in str(e1) or "market_" in str(e1):
                    raise
                print(f"[LAST120] {ticker}: fractional 'count' refused ({e1}) -- retrying as count_fp.")
                body = {"ticker": ticker, "client_order_id": f"pulse-surge-last120-{uuid.uuid4()}", "side": side,
                        "count_fp": f"{qty:.2f}", "price": f"{yes_price:.2f}", "time_in_force": "immediate_or_cancel",
                        "self_trade_prevention_type": "taker_at_cross"}
                print(f"  [RAW ORDER BODY]: {body}")
                client._request("POST", "/portfolio/events/orders", json_body=body)
        except Exception as e:
            print(f"[LAST120] {ticker}: entry order failed ({e}).")
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
            print(f"[LAST120] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry while it qualifies.")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                             "flip_star": False, "surge": True, "last120": True,
                             "bad_fill": entry_price < LAST120_MIN_PRICE - 0.005}
        if entry_price < LAST120_MIN_PRICE - 0.005:
            print(f"[LAST120] {ticker}: filled at ${entry_price:.2f}, under the {LAST120_MIN_PRICE*100:.0f}c minimum -- "
                  f"price is falling fast, selling right away (11 Nov, Matt).")
        print(f"[LAST120] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- 76% stop, sells at 99c, else held to the end.")
