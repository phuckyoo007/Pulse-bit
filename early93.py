"""
EARLY93 / "Early settle" (11 Nov 01:56 CT, Matt: top-3 strategy, 2 shares) -- from the board recordings:
the board's pick bought at 93-98c with 7:00-4:00 left and held to the end won 97% of 179 test rounds (+$15.30 at 5 shares
over four sessions). Exit: NONE -- held to the end. One bet per round (the round is then skipped by EARLY93/LATE60).
"""
import time
import uuid

import flip_star
import strategy_guard
import kalshi_ws

EARLY93_ENABLED = True   # 12 Nov 08:43 CT (Matt): run strategies 1-3 (BTC92, Follow the crowd, Early settle) at 1 share
EARLY93_SHARES = 2   # 12 Nov 08:43 CT (Matt): everything at 2 shares
EARLY93_DOLLARS = 0   # 10 Nov 18:02 CT (Matt): whole shares again (was about 11c per bet)
EARLY93_MAX_SECONDS_LEFT = 420
EARLY93_MIN_SECONDS_LEFT = 240
EARLY93_MIN_PRICE = 0.96   # 12 Nov 14:53 CT: was 93c -- 93-95c lost -$2.19 on paper, 96c+ +$2.34
EARLY93_MAX_PRICE = 0.98
# 12 Nov 15:58 CT (Matt: tighten on every loss). All 7 Early settle losses since 11 Nov were bought with the board at
# 90-95% win; none of the ~50 bought at 96%+ lost. And 3 losers FILLED under the minimum (91, 92, 90c) = price falling.
EARLY93_MIN_WIN = 0.96

_done = {}   # ticker -> time (one bet per round)
_tries = {}
_note = {}


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    if not EARLY93_ENABLED or flip_star.in_pause():
        return
    b = flip_star.board()
    if not b:
        return
    now = time.time()
    try:
        kalshi_ws.watch_extra([t for t, x in b.items() if "15M" in t.split("-")[0].upper()
                               and not flip_star.not_allowed_to_buy(x.get("coin"), t)])
    except Exception:
        pass
    for t in [t for t, at in _done.items() if now - at > 1800]:
        _done.pop(t, None)
        _tries.pop(t, None)
    for ticker, n in flip_star.by_priority(b):
        if "15M" not in ticker.split("-")[0].upper() or flip_star.not_allowed_to_buy(n.get("coin"), ticker):
            continue   # 7 Nov 14:10 CT (Matt): Bitcoin + the coin with the 2nd-biggest volume
        tl = n.get("tl")
        if tl is None or tl > EARLY93_MAX_SECONDS_LEFT or tl < EARLY93_MIN_SECONDS_LEFT:
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        if not strategy_guard.can_enter(ticker, state, "EARLY93"):
            continue   # 12 Nov: one coin per round + cooldown after a loss
        if (n.get("p") or 0) < EARLY93_MIN_WIN - 1e-9:
            continue   # 12 Nov: board must give 96%+ (every loss was 90-95%)
        side_name = n.get("side")
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
                _log_once(ticker + ":lq", f"[EARLY93] {ticker}: no live price yet -- waiting for the stream before buying.")
                continue
            _lc = _lq[1] if side == "bid" else 1 - _lq[0]
            if abs(_lc - cost) > 0.02 + 1e-9:
                _log_once(ticker + ":lqd", f"[EARLY93] {ticker}: live price {_lc*100:.0f}c vs lookup {cost*100:.0f}c -- price is "
                                           f"moving fast, not buying this pass.")
                continue
            if cost < EARLY93_MIN_PRICE - 1e-9 or cost > EARLY93_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[EARLY93] {ticker}: board pick {side_name.upper()} but price {cost*100:.0f}c is "
                                  f"outside {EARLY93_MIN_PRICE*100:.0f}-{EARLY93_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            limit = min(EARLY93_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[EARLY93] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        strategy_guard.note_entry(ticker)
        qty = max(0.01, int(EARLY93_DOLLARS / limit * 100) / 100) if EARLY93_DOLLARS else float(EARLY93_SHARES)
        print(f"[EARLY93] ENTRY {ticker} ({n.get('coin')}): board pick {side_name.upper()} ({(n.get('p') or 0)*100:.0f}%), "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {qty:.2f} share up to ${limit:.2f} "
              f"(about ${qty*limit:.2f})")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * qty)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            coid = f"pulse-surge-early93-{uuid.uuid4()}"
            try:
                client.create_order(ticker=ticker, client_order_id=coid, side=side, count=(str(int(qty)) if abs(qty - round(qty)) < 1e-9 else f"{qty:.2f}"),
                                    price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
            except Exception as e1:
                if "insufficient_balance" in str(e1) or "market_" in str(e1):
                    raise
                print(f"[EARLY93] {ticker}: fractional 'count' refused ({e1}) -- retrying as count_fp.")
                body = {"ticker": ticker, "client_order_id": f"pulse-surge-early93-{uuid.uuid4()}", "side": side,
                        "count_fp": f"{qty:.2f}", "price": f"{yes_price:.2f}", "time_in_force": "immediate_or_cancel",
                        "self_trade_prevention_type": "taker_at_cross"}
                print(f"  [RAW ORDER BODY]: {body}")
                client._request("POST", "/portfolio/events/orders", json_body=body)
        except Exception as e:
            print(f"[EARLY93] {ticker}: entry order failed ({e}).")
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
            print(f"[EARLY93] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry while it qualifies.")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                             "flip_star": False, "surge": True, "early93": True,
                             "bad_fill": entry_price < EARLY93_MIN_PRICE - 0.005}   # 12 Nov: ANY fill under the minimum = falling -> sell now
        if entry_price < EARLY93_MIN_PRICE - 0.005:
            print(f"[EARLY93] {ticker}: filled at ${entry_price:.2f}, under the {EARLY93_MIN_PRICE*100:.0f}c minimum -- "
                  f"price is falling fast, selling right away (11 Nov, Matt).")
        print(f"[EARLY93] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- no stop, held to the end.")
