"""
LAST3 strategy -- REAL MONEY, 1 share (12 Nov 16:11 CT, Matt: "run a real version at 1 share").
Last ~3 minutes of a 15-minute round, using the edge board AND what the other coins are doing:

  * LAST3_MIN_SECONDS_LEFT..LAST3_MAX_SECONDS_LEFT left (2:00-3:00).
  * Pick the side (YES = up / NO = down) that at least LAST3_MIN_AGREE of the OTHER six coins' board picks point to.
  * The board's own win % for that side must be at least LAST3_MIN_EDGE above the price we pay (the board thinks the
    price is too cheap), and the price must be LAST3_MIN_PRICE..LAST3_MAX_PRICE.
  * One coin per round across all strategies, 30-min pause after any loss, -$4 loss limit (strategy_guard).
  * Exit (bot.py): held to the end; 40% stop; a fill under the minimum price sells at once.
Paper twin: hour_study "L3-BOTH".
"""
import time
import uuid

import flip_star
import strategy_guard
import kalshi_ws

LAST3_ENABLED = True   # 12 Nov 16:11 CT (Matt): real money, 1 share
LAST3_SHARES = 1
LAST3_DOLLARS = 0
LAST3_MAX_SECONDS_LEFT = 180
LAST3_MIN_SECONDS_LEFT = 120
LAST3_MIN_PRICE = 0.50
LAST3_MAX_PRICE = 0.92
LAST3_MIN_AGREE = 5      # of the other 6 coins
LAST3_MIN_EDGE = 0.05    # board win % minus price

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
    if not LAST3_ENABLED or flip_star.in_pause():
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
        if tl is None or tl > LAST3_MAX_SECONDS_LEFT or tl < LAST3_MIN_SECONDS_LEFT:
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        if not strategy_guard.can_enter(ticker, state, "LAST3"):
            continue   # 12 Nov: one coin per round + cooldown after a loss
        rid = ticker.split("-")[1] if "-" in ticker else ticker
        others = [m for t, m in b.items() if t != ticker and "15M" in t.split("-")[0].upper()
                  and (t.split("-")[1] if "-" in t else t) == rid and m.get("side") in ("yes", "no")]
        ups = sum(1 for m in others if m.get("side") == "yes")
        downs = len(others) - ups
        if max(ups, downs) < LAST3_MIN_AGREE:
            continue
        side_name = "yes" if ups > downs else "no"
        p_side = (n.get("p") or 0) if n.get("side") == side_name else 1 - (n.get("p") or 0)
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
                _log_once(ticker + ":lq", f"[LAST3] {ticker}: no live price yet -- waiting for the stream before buying.")
                continue
            _lc = _lq[1] if side == "bid" else 1 - _lq[0]
            if abs(_lc - cost) > 0.02 + 1e-9:
                _log_once(ticker + ":lqd", f"[LAST3] {ticker}: live price {_lc*100:.0f}c vs lookup {cost*100:.0f}c -- price is "
                                           f"moving fast, not buying this pass.")
                continue
            if cost < LAST3_MIN_PRICE - 1e-9 or cost > LAST3_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[LAST3] {ticker}: board pick {side_name.upper()} but price {cost*100:.0f}c is "
                                  f"outside {LAST3_MIN_PRICE*100:.0f}-{LAST3_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            if p_side - cost < LAST3_MIN_EDGE - 1e-9:
                _log_once(ticker + ":edge", f"[LAST3] {ticker}: {max(ups, downs)}/{len(others)} coins say {side_name.upper()}, "
                                            f"board {p_side*100:.0f}% vs price {cost*100:.0f}c -- not enough edge.")
                continue
            limit = min(LAST3_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[LAST3] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        strategy_guard.note_entry(ticker)
        qty = max(0.01, int(LAST3_DOLLARS / limit * 100) / 100) if LAST3_DOLLARS else float(LAST3_SHARES)
        print(f"[LAST3] ENTRY {ticker} ({n.get('coin')}): {max(ups, downs)}/{len(others)} coins say {side_name.upper()}, board {p_side*100:.0f}%, "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {qty:.2f} share up to ${limit:.2f} "
              f"(about ${qty*limit:.2f})")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * qty)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            coid = f"pulse-surge-last3-{uuid.uuid4()}"
            try:
                client.create_order(ticker=ticker, client_order_id=coid, side=side, count=(str(int(qty)) if abs(qty - round(qty)) < 1e-9 else f"{qty:.2f}"),
                                    price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
            except Exception as e1:
                if "insufficient_balance" in str(e1) or "market_" in str(e1):
                    raise
                print(f"[LAST3] {ticker}: fractional 'count' refused ({e1}) -- retrying as count_fp.")
                body = {"ticker": ticker, "client_order_id": f"pulse-surge-last3-{uuid.uuid4()}", "side": side,
                        "count_fp": f"{qty:.2f}", "price": f"{yes_price:.2f}", "time_in_force": "immediate_or_cancel",
                        "self_trade_prevention_type": "taker_at_cross"}
                print(f"  [RAW ORDER BODY]: {body}")
                client._request("POST", "/portfolio/events/orders", json_body=body)
        except Exception as e:
            print(f"[LAST3] {ticker}: entry order failed ({e}).")
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
            print(f"[LAST3] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry while it qualifies.")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                             "flip_star": False, "surge": True, "last3": True,
                             "bad_fill": entry_price < LAST3_MIN_PRICE - 0.005}
        if entry_price < LAST3_MIN_PRICE - 0.005:
            print(f"[LAST3] {ticker}: filled at ${entry_price:.2f}, under the {LAST3_MIN_PRICE*100:.0f}c minimum -- "
                  f"price is falling fast, selling right away (11 Nov, Matt).")
        print(f"[LAST3] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- held to the end (40% stop).")
