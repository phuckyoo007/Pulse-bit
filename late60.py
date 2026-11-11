"""
LATE60 strategy (10 Nov 18:03 CT, Matt: "build running at one share") -- found in the board recordings and checked on
three separate days: the board's pick bought at 60-80c with 2:00-4:00 left WHILE the momentum meter is moving our way,
held to the end, won 83% of 65 test rounds (+11.7c/share; positive on all three days).

  * 15-minute crypto rounds the bot is allowed to buy (currently BTC + XRP).
  * With 4:00-2:00 left, buy LATE60_SHARES of the board's pick if it costs 60-80c and momentum for our side > +5.
  * One bet per round. Exit: NONE -- held to the end.
"""
import math
import time
import uuid

import flip_star
import surge

LATE60_ENABLED = False   # 11 Nov 00:00 CT (Matt): dormant -- only Follow the crowd (LATE80) runs
LATE60_PAPER = True   # 10 Nov 20:57 CT (Matt): PAPER only for now -- no real orders, results logged as [LATE60-PAPER]
LATE60_SHARES = 5   # 10 Nov 19:14 CT (Matt): 5 shares (was 1)
LATE60_DOLLARS = 0
LATE60_MIN_MOMENTUM = 0.05
LATE60_MAX_SECONDS_LEFT = 240
LATE60_MIN_SECONDS_LEFT = 120
LATE60_MIN_PRICE = 0.60
LATE60_MAX_PRICE = 0.80

_done = {}   # ticker -> time (one bet per round)
_tries = {}
_note = {}
_paper = {}   # ticker -> {side, cost, qty, close_at}
_ptot = {"n": 0, "won": 0, "pnl": 0.0}


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


def _settle_paper(client, now):
    for t, p in list(_paper.items()):
        if now < p["close_at"] + 60:
            continue
        try:
            res = client.get_market(t).get("market", {}).get("result")
        except Exception:
            continue
        if res not in ("yes", "no"):
            if now > p["close_at"] + 1800:
                _paper.pop(t, None)
            continue
        _paper.pop(t, None)
        won = res == p["side"]
        fee = math.ceil(0.07 * p["cost"] * (1 - p["cost"]) * p["qty"] * 100 - 1e-9) / 100
        pnl = ((1.0 if won else 0.0) - p["cost"]) * p["qty"] - fee
        _ptot["n"] += 1
        _ptot["won"] += 1 if won else 0
        _ptot["pnl"] += pnl
        print(f"[LATE60-PAPER] RESULT {t}: {'WON' if won else 'LOST'} {pnl:+.2f} -- LATE60 paper total {_ptot['n']} bets, "
              f"{_ptot['won']} won, {_ptot['pnl']:+.2f}")


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    if not LATE60_ENABLED or flip_star.in_pause():
        return
    b = flip_star.board()
    if not b:
        return
    now = time.time()
    if LATE60_PAPER:
        _settle_paper(client, now)
    for t in [t for t, at in _done.items() if now - at > 1800]:
        _done.pop(t, None)
        _tries.pop(t, None)
    for ticker, n in b.items():
        if "15M" not in ticker.split("-")[0].upper() or flip_star.not_allowed_to_buy(n.get("coin"), ticker):
            continue   # 7 Nov 14:10 CT (Matt): Bitcoin + the coin with the 2nd-biggest volume
        tl = n.get("tl")
        if tl is None or tl > LATE60_MAX_SECONDS_LEFT or tl < LATE60_MIN_SECONDS_LEFT:
            continue
        if ticker in _done or (not LATE60_PAPER and ticker in state) or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        side_name = n.get("side")
        side = "bid" if side_name == "yes" else "ask"
        _mom = surge.momentum_for(ticker, side_name)
        if _mom is None or _mom <= LATE60_MIN_MOMENTUM + 1e-9:
            continue   # momentum must be moving our way
        try:
            if not LATE60_PAPER:
                held, _ = flip_star._held_count(client, ticker)
                if held != 0:
                    continue
            mk = client.get_market(ticker).get("market", {})
            ya, yb = float(mk.get("yes_ask_dollars")), float(mk.get("yes_bid_dollars"))
            if ya - yb > flip_star.FLIP_MAX_SPREAD + 1e-9:
                continue
            cost = ya if side == "bid" else 1 - yb
            if cost < LATE60_MIN_PRICE - 1e-9 or cost > LATE60_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[LATE60] {ticker}: board pick {side_name.upper()} but price {cost*100:.0f}c is "
                                  f"outside {LATE60_MIN_PRICE*100:.0f}-{LATE60_MAX_PRICE*100:.0f}c -- waiting.")
                continue
            limit = min(LATE60_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[LATE60] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        if LATE60_PAPER:
            _paper[ticker] = {"side": side_name, "cost": cost, "qty": float(LATE60_SHARES), "close_at": now + float(tl)}
            print(f"[LATE60-PAPER] BUY {ticker} ({n.get('coin')}): board pick {side_name.upper()} @ {cost*100:.0f}c, "
                  f"momentum {_mom*100:+.0f}, {int(tl)}s left -- {LATE60_SHARES} shares (paper), held to the end")
            continue
        qty = max(0.01, int(LATE60_DOLLARS / limit * 100) / 100) if LATE60_DOLLARS else float(LATE60_SHARES)
        print(f"[LATE60] ENTRY {ticker} ({n.get('coin')}): board pick {side_name.upper()} ({(n.get('p') or 0)*100:.0f}%, momentum {_mom*100:+.0f}), "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {qty:.2f} share up to ${limit:.2f} "
              f"(about ${qty*limit:.2f})")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * qty)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            coid = f"pulse-surge-late60-{uuid.uuid4()}"
            try:
                client.create_order(ticker=ticker, client_order_id=coid, side=side, count=(str(int(qty)) if abs(qty - round(qty)) < 1e-9 else f"{qty:.2f}"),
                                    price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
            except Exception as e1:
                if "insufficient_balance" in str(e1) or "market_" in str(e1):
                    raise
                print(f"[LATE60] {ticker}: fractional 'count' refused ({e1}) -- retrying as count_fp.")
                body = {"ticker": ticker, "client_order_id": f"pulse-surge-late60-{uuid.uuid4()}", "side": side,
                        "count_fp": f"{qty:.2f}", "price": f"{yes_price:.2f}", "time_in_force": "immediate_or_cancel",
                        "self_trade_prevention_type": "taker_at_cross"}
                print(f"  [RAW ORDER BODY]: {body}")
                client._request("POST", "/portfolio/events/orders", json_body=body)
        except Exception as e:
            print(f"[LATE60] {ticker}: entry order failed ({e}).")
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
            print(f"[LATE60] {ticker}: nothing filled at ${limit:.2f} (price moved) -- will retry while it qualifies.")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                             "flip_star": False, "surge": True, "late60": True}
        print(f"[LATE60] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- no stop, held to the end.")
