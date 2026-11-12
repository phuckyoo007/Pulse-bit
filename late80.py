"""
LATE80 strategy (10 Nov 16:46 CT, Matt: "put it to a real test at three shares") -- built from the board recordings:
the board's pick bought at 80-93c with 2:00-4:00 left and held to the end won 94% of 108 test rounds (+7.5c/share).

  * 15-minute crypto rounds the bot is allowed to buy (currently BTC + XRP).
  * With LATE80_MAX_SECONDS_LEFT..LATE80_MIN_SECONDS_LEFT left, buy LATE80_SHARES of the board's pick if it costs
    LATE80_MIN_PRICE..LATE80_MAX_PRICE. One bet per round.
  * Exit: NONE -- held to the end. In testing every stop (86%, 70%, 60%, 50%) made less than holding.

(The BTC92 notes below describe the module this was copied from.)
BTC92 strategy (7 Nov 11:30 CT, Matt).

  * Bitcoin 15-minute rounds only.
  * In the last BTC92_WINDOW seconds (2:00), when the board's pick for BTC is at 92%+ win AND its price is 92c+ (never
    above 98c -- a 99c share can't pay for itself), buy LATE80_SHARES (5) of that side. One bet per round.
  * Exit (bot.py, positions flagged "btc92"): instant stop if our side falls to 82% of what we paid; otherwise hold to
    the end. No other stop touches it.
"""
import time
import uuid

import flip_star
import kalshi_ws

LATE80_ENABLED = True   # 12 Nov 08:43 CT (Matt): run strategies 1-3 (BTC92, Follow the crowd, Early settle) at 1 share
LATE80_SHARES = 2   # 12 Nov 08:43 CT (Matt): everything at 2 shares
LATE80_DOLLARS = 0   # 10 Nov 18:02 CT (Matt): whole shares again (was about 11c per bet)
LATE80_MAX_SECONDS_LEFT = 240
LATE80_MIN_SECONDS_LEFT = 120
LATE80_MIN_PRICE = 0.80
LATE80_MAX_PRICE = 0.93

_done = {}   # ticker -> time (one bet per round)
BREAKER_ARMED = False   # 11 Nov 02:42 CT (Matt): Flip Star + BTC92 are on already -- breaker not needed
_tries = {}
_note = {}


def record(position, pnl) -> None:
    """Circuit breaker: called for every closed bet. The first losing LATE80 bet trips it."""
    global BREAKER_ARMED
    try:
        if not isinstance(position, dict) or not position.get("late80") or not BREAKER_ARMED:
            return
        if float(pnl) < 0:
            import btc92
            BREAKER_ARMED = False   # 11 Nov 00:02 CT (Matt): LATE80 stays ON after the breaker trips
            flip_star.FLIP_ENABLED = True
            btc92.BTC92_ENABLED = True
            print(f"[BREAKER] TRIPPED -- Follow the crowd (LATE80) lost {float(pnl):+.2f}. LATE80 stays on; Flip Star and BTC92 now ON too.")
        else:
            print(f"[BREAKER] LATE80 bet closed {float(pnl):+.2f} -- breaker still armed (trips on the first loss).")
    except Exception as e:
        print(f"[BREAKER] check failed ({e}).")


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    if not LATE80_ENABLED or flip_star.in_pause():
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
        if tl is None or tl > LATE80_MAX_SECONDS_LEFT or tl < LATE80_MIN_SECONDS_LEFT:
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
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
                _log_once(ticker + ":lq", f"[LATE80] {ticker}: no live price yet -- waiting for the stream before buying.")
                continue
            _lc = _lq[1] if side == "bid" else 1 - _lq[0]
            if abs(_lc - cost) > 0.02 + 1e-9:
                _log_once(ticker + ":lqd", f"[LATE80] {ticker}: live price {_lc*100:.0f}c vs lookup {cost*100:.0f}c -- price is "
                                           f"moving fast, not buying this pass.")
                continue
            if cost < LATE80_MIN_PRICE - 1e-9 or cost > LATE80_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[LATE80] {ticker}: board pick {side_name.upper()} but price {cost*100:.0f}c is "
                                  f"outside {LATE80_MIN_PRICE*100:.0f}-{LATE80_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            limit = min(LATE80_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[LATE80] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        qty = max(0.01, int(LATE80_DOLLARS / limit * 100) / 100) if LATE80_DOLLARS else float(LATE80_SHARES)
        print(f"[LATE80] ENTRY {ticker} ({n.get('coin')}): board pick {side_name.upper()} ({(n.get('p') or 0)*100:.0f}%), "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {qty:.2f} share up to ${limit:.2f} "
              f"(about ${qty*limit:.2f})")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * qty)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            coid = f"pulse-surge-late80-{uuid.uuid4()}"
            try:
                client.create_order(ticker=ticker, client_order_id=coid, side=side, count=(str(int(qty)) if abs(qty - round(qty)) < 1e-9 else f"{qty:.2f}"),
                                    price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
            except Exception as e1:
                if "insufficient_balance" in str(e1) or "market_" in str(e1):
                    raise
                print(f"[LATE80] {ticker}: fractional 'count' refused ({e1}) -- retrying as count_fp.")
                body = {"ticker": ticker, "client_order_id": f"pulse-surge-late80-{uuid.uuid4()}", "side": side,
                        "count_fp": f"{qty:.2f}", "price": f"{yes_price:.2f}", "time_in_force": "immediate_or_cancel",
                        "self_trade_prevention_type": "taker_at_cross"}
                print(f"  [RAW ORDER BODY]: {body}")
                client._request("POST", "/portfolio/events/orders", json_body=body)
        except Exception as e:
            print(f"[LATE80] {ticker}: entry order failed ({e}).")
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
            print(f"[LATE80] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry while it qualifies.")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                             "flip_star": False, "surge": True, "late80": True,
                             "bad_fill": entry_price < LATE80_MIN_PRICE - 0.05}
        if entry_price < LATE80_MIN_PRICE - 0.05:
            print(f"[LATE80] {ticker}: filled at ${entry_price:.2f}, more than 5c under the {LATE80_MIN_PRICE*100:.0f}c minimum -- "
                  f"price is falling fast, selling right away (11 Nov, Matt).")
        print(f"[LATE80] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- no stop, held to the end.")
