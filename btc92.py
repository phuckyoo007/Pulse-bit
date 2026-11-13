"""
BTC92 strategy (7 Nov 11:30 CT, Matt).

  * Bitcoin 15-minute rounds only.
  * In the last BTC92_WINDOW seconds (2:00), when the board's pick for BTC is at 92%+ win AND its price is 92c+ (never
    above 98c -- a 99c share can't pay for itself), buy BTC92_SHARES (5) of that side. One bet per round.
  * Exit (bot.py, positions flagged "btc92"): instant stop if our side falls to 82% of what we paid; otherwise hold to
    the end. No other stop touches it.
"""
import time
import uuid

import flip_star
import strategy_guard

BTC92_ENABLED = True   # 11 Nov 02:42 CT (Matt): back on
BTC92_SHARES = 4   # 12 Nov 23:39 CT (Matt): double the shares (was 2)
BTC92_HOLD_TO_END = True   # 11 Nov 02:46 CT (Matt): no stop -- held to the end
BTC92_WINDOW = 120          # last 2 minutes
BTC92_MIN_SECONDS_LEFT = 10
BTC92_MIN_WIN = 0.96   # 12 Nov 15:58 CT: was 92% -- board must agree with the 94c+ price (same lesson as Early settle)
BTC92_MIN_PRICE = 0.94   # 12 Nov 14:53 CT: was 92c -- 92-93c lost -$2.72 on paper, 94c+ +$8.75
BTC92_MAX_PRICE = 0.98
BTC92_STOP_FRACTION = 0.78  # 10 Nov 23:27 CT (Matt): firm instant stop at 78% of entry (was 86%)
BTC92_MIN_SIGMA = 1.5       # 7 Nov 14:09 CT (Matt): only when 'To flip' is GREEN (2+) or YELLOW 1 (1.5-2 sigma)

_done = {}   # ticker -> time (one bet per round)
_tries = {}
_note = {}


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    if not BTC92_ENABLED or flip_star.in_pause() or not flip_star.hour_ok("btc92"):
        return   # 12 Nov: only in its good hours (flip_star.STRATEGY_HOURS)
    b = flip_star.board()
    if not b:
        return
    now = time.time()
    for t in [t for t, at in _done.items() if now - at > 1800]:
        _done.pop(t, None)
        _tries.pop(t, None)
    for ticker, n in flip_star.by_priority(b):
        if "15M" not in ticker.split("-")[0].upper() or flip_star.not_allowed_to_buy(n.get("coin"), ticker):
            continue   # 7 Nov 14:10 CT (Matt): Bitcoin + the coin with the 2nd-biggest volume
        tl = n.get("tl")
        if tl is None or tl > BTC92_WINDOW or tl < BTC92_MIN_SECONDS_LEFT:
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        if not strategy_guard.can_enter(ticker, state, "BTC92"):
            continue   # 12 Nov: one coin per round + cooldown after a loss
        _unf = n.get("flips") == 0   # 10 Nov 17:39 CT (Matt): a round that hasn't flipped can be bought at 80c+ / 80%+
        _min_win = BTC92_MIN_WIN   # 12 Nov: no more 80% shortcut for unflipped rounds
        _min_px = BTC92_MIN_PRICE
        if (n.get("p") or 0) < _min_win - 1e-9:
            continue
        if n.get("flips") is None or n["flips"] > flip_star.FLIP_MAX_ROUND_FLIPS:
            _log_once(ticker + ":f", f"[BTC92] {ticker}: {n['p']*100:.0f}% but it has flipped {n.get('flips')} times this round "
                                     f"(max {flip_star.FLIP_MAX_ROUND_FLIPS}) -- not buying.")
            continue   # 8 Nov 11:44 CT (Matt): XRP had flipped 13 times before the 6:13 AM XRP92 loss
        if (n.get("zs") if n.get("zs") is not None else -9) < BTC92_MIN_SIGMA - 1e-9:   # 12 Nov: sigma check for every round
            _log_once(ticker + ":z", f"[BTC92] {ticker}: {n['p']*100:.0f}% but To flip is only {n.get('zs', 0):.1f} sigma "
                                     f"(needs green or Yellow 1, {BTC92_MIN_SIGMA:.1f}+) -- not buying.")
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
            if cost < _min_px - 1e-9 or cost > BTC92_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[BTC92] {ticker}: {side_name.upper()} at {n['p']*100:.0f}% but price {cost*100:.0f}c is "
                                  f"outside {_min_px*100:.0f}-{BTC92_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            limit = min(BTC92_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[BTC92] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        strategy_guard.note_entry(ticker)
        print(f"[BTC92] ENTRY {ticker} ({n.get('coin')}): {side_name.upper()} at {n['p']*100:.0f}%, {int(tl)}s left -- "
              f"{'DRY-RUN ' if dry_run else ''}buying {BTC92_SHARES} up to ${limit:.2f}")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * BTC92_SHARES)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            client.create_order(ticker=ticker, client_order_id=f"pulse-surge-btc92-{uuid.uuid4()}", side=side,
                                count=str(BTC92_SHARES), price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
        except Exception as e:
            print(f"[BTC92] {ticker}: entry order failed ({e}).")
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
            print(f"[BTC92] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry while it qualifies.")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                             "flip_star": False, "surge": True, "btc92": True}
        print(f"[BTC92] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- stop at "
              f"{BTC92_STOP_FRACTION*100:.0f}% (${entry_price*BTC92_STOP_FRACTION:.2f}), otherwise hold to the end.")
