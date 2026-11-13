"""
RIDE82 -- REAL MONEY (13 Nov 03:33 CT, Matt): "in the last 3 1/2-4 minutes, anything over 80% that looks like it keeps
riding to 100 -- bid on Bitcoin and XRP (and DOGE) the second they hit 82c or above with less than four minutes left,
40% stop loss."

  * 13 Nov 03:35 CT (Matt): every coin's 15-minute round (all nine), 1 share, one bet per coin per round.
  * Under RIDE82_MAX_SECONDS_LEFT (4:00) left, down to RIDE82_MIN_SECONDS_LEFT.
  * The board's pick, priced RIDE82_MIN_PRICE..RIDE82_MAX_PRICE (82-97c).
  * "Seems like it continues riding": our side's price must not have fallen over the last minute.
  * Held to the end with the 40% stop (bot.py); a fill under 82c sells at once.
  * It has its OWN slot each round (one RIDE82 bet per round), so Early settle's bet doesn't block it;
    the 30-min pause after any loss and the -$4 loss limit still apply.
"""
import time
import uuid

import flip_star
import strategy_guard
import kalshi_ws

RIDE82_ENABLED = True   # 13 Nov 03:33 CT (Matt): real money
RIDE82_SHARES = 1   # 13 Nov 03:35 CT (Matt): one share
RIDE82_DOLLARS = 0
RIDE82_COINS = None   # 13 Nov 03:35 CT (Matt): EVERY coin on the board (all nine, ETH + NEAR included), one bet per coin per round
RIDE82_MAX_SECONDS_LEFT = 240
RIDE82_MIN_SECONDS_LEFT = 15
# 13 Nov 09:31 CT (auto loss review): all 4 Ride 82 losses so far were DOGE (09:57Z 82c, 10:41Z 84c, 12:29Z 82c,
# 12:43Z 95c) -- a higher DOGE price floor wouldn't have stopped the 95c one, so DOGE is out of Ride 82.
RIDE82_SKIP_COINS = {"DOGE"}
# 13 Nov 11:33 CT (auto loss review): XRP 84c (board 85%), SOL 86c (88%), BTC 95c (94%) all lost -- every Ride 82 loss
# was bought with the board within about 2 points of the price. Now the board must be 3+ points above the price.
RIDE82_MIN_EDGE = 0.01   # 13 Nov 13:49 CT (Matt): only a 1-point difference (was 3)
RIDE82_MIN_PRICE = 0.82
RIDE82_MAX_PRICE = 0.97

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
    if not RIDE82_ENABLED or flip_star.in_pause():
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
        if "15M" not in ticker.split("-")[0].upper():   # 13 Nov: every coin -- no coin filter for RIDE82
            continue   # 7 Nov 14:10 CT (Matt): Bitcoin + the coin with the 2nd-biggest volume
        tl = n.get("tl")
        if tl is None or tl > RIDE82_MAX_SECONDS_LEFT or tl < RIDE82_MIN_SECONDS_LEFT:
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        if not strategy_guard.can_enter(ticker, state, "RIDE82-" + str(n.get("coin")), own_slot=True):
            continue   # 12 Nov: one coin per round + cooldown after a loss
        if RIDE82_COINS and n.get("coin") not in RIDE82_COINS:
            continue
        if n.get("coin") in RIDE82_SKIP_COINS:
            continue
        side_name = n.get("side")
        if side_name not in ("yes", "no"):
            continue
        _mc = n.get("mid_chg_1m")
        if _mc is not None and _mc * (1 if side_name == "yes" else -1) < -1e-9:
            continue   # our side's price fell over the last minute -- not "riding"
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
                _log_once(ticker + ":lq", f"[RIDE82] {ticker}: no live price yet -- waiting for the stream before buying.")
                continue
            _lc = _lq[1] if side == "bid" else 1 - _lq[0]
            if abs(_lc - cost) > 0.02 + 1e-9:
                _log_once(ticker + ":lqd", f"[RIDE82] {ticker}: live price {_lc*100:.0f}c vs lookup {cost*100:.0f}c -- price is "
                                           f"moving fast, not buying this pass.")
                continue
            if cost < RIDE82_MIN_PRICE - 1e-9 or cost > RIDE82_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[RIDE82] {ticker}: board pick {side_name.upper()} but price {cost*100:.0f}c is "
                                  f"outside {RIDE82_MIN_PRICE*100:.0f}-{RIDE82_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            if (n.get("p") or 0) < cost + RIDE82_MIN_EDGE - 1e-9:
                _log_once(ticker + ":bp", f"[RIDE82] {ticker}: board {(n.get('p') or 0)*100:.0f}% is below the {cost*100:.0f}c "
                                          f"price + {RIDE82_MIN_EDGE*100:.0f} pts -- skipping (13 Nov: losses were all bought with the board 0-2 pts over the price).")
                continue
            limit = min(RIDE82_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[RIDE82] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        strategy_guard.note_entry(ticker, own_slot="RIDE82-" + str(n.get("coin")))
        qty = max(0.01, int(RIDE82_DOLLARS / limit * 100) / 100) if RIDE82_DOLLARS else float(RIDE82_SHARES)
        print(f"[RIDE82] ENTRY {ticker} ({n.get('coin')}): board pick {side_name.upper()} ({(n.get('p') or 0)*100:.0f}%), "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {qty:.2f} share up to ${limit:.2f} "
              f"(about ${qty*limit:.2f})")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * qty)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            coid = f"pulse-surge-ride82-{uuid.uuid4()}"
            try:
                client.create_order(ticker=ticker, client_order_id=coid, side=side, count=(str(int(qty)) if abs(qty - round(qty)) < 1e-9 else f"{qty:.2f}"),
                                    price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
            except Exception as e1:
                if "insufficient_balance" in str(e1) or "market_" in str(e1):
                    raise
                print(f"[RIDE82] {ticker}: fractional 'count' refused ({e1}) -- retrying as count_fp.")
                body = {"ticker": ticker, "client_order_id": f"pulse-surge-ride82-{uuid.uuid4()}", "side": side,
                        "count_fp": f"{qty:.2f}", "price": f"{yes_price:.2f}", "time_in_force": "immediate_or_cancel",
                        "self_trade_prevention_type": "taker_at_cross"}
                print(f"  [RAW ORDER BODY]: {body}")
                client._request("POST", "/portfolio/events/orders", json_body=body)
        except Exception as e:
            print(f"[RIDE82] {ticker}: entry order failed ({e}).")
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
            print(f"[RIDE82] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry while it qualifies.")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                             "flip_star": False, "surge": True, "ride82": True,
                             "bad_fill": entry_price < RIDE82_MIN_PRICE - 0.005}
        if entry_price < RIDE82_MIN_PRICE - 0.005:
            print(f"[RIDE82] {ticker}: filled at ${entry_price:.2f}, under the {RIDE82_MIN_PRICE*100:.0f}c minimum -- "
                  f"price is falling fast, selling right away (11 Nov, Matt).")
        print(f"[RIDE82] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- held to the end (40% stop).")
