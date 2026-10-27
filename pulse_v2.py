"""
PULSE V2 -- PAPER TRIAL (no real orders, ever, while V2_PAPER is True).

Runs inside bot.py next to the live flip-star strategy and reads the same Crypto Edge Board numbers, but trades a
SIMULATED bankroll so the two designs can be compared side by side. Everything is logged with the prefix [V2-PAPER].

Design (25 Oct, from the Oct 18-25 trade review):
  1. EDGE FILTER: only buy when the model's win chance beats the price by V2_MIN_EDGE after the Kalshi fee.
     (99c at 98% win loses money on average even though it usually wins.)
  2. RISK SIZING: each bet risks V2_RISK_FRACTION of the paper balance, assuming a bad gap can cost
     V2_GAP_LOSS per share before a stop fills (NEAR sold at 18c, DOGE at 41c).
  3. ROUND CAP: all open paper bets in the same 15-minute round together may not cost more than
     V2_ROUND_CAP_FRACTION of the balance (coins crash together).
  4. AUTO-BENCH: a coin with V2_BENCH_LOSSES losing bets inside 24h sits out for V2_BENCH_HOURS.
  5. THREE EXITS ONLY: To-flip sigma for our side <= V2_EXIT_SIGMA for V2_EXIT_CONFIRM_SECONDS, an instant gap stop
     V2_GAP_STOP under the entry price, otherwise hold to settlement (94-98c bets pay the full $1).
  6. AFTERMATH: every early exit (paper AND live Pulse) is followed to settlement and logged as
     "[V2-AFTERMATH] ... held would have WON/LOST" so the stops can be judged on facts.
  7. DAILY STOP: no new paper bets after the paper balance is down V2_DAILY_STOP_FRACTION on the (Central) day.
  8. Paper first. Nothing here sends an order.

Quotes come from the board (yes_ask / no_ask) so this adds no Kalshi API load except one market read per
position after its window closes (to learn the result).
"""
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import flip_star

V2_ENABLED = True
V2_PAPER = False                 # 26 Oct 19:42 CT (Matthew): "try V2 alongside Pulse" -- LIVE, small (V2_LIVE_SHARES)
V2_LIVE_SHARES = 5               # fixed shares per live bet (paper used 5%-risk sizing)
V2_LIVE_MAX_OPEN = 2             # at most this many live V2 bets open at once
V2_LIVE_DAILY_STOP = 10.0        # no new live V2 bets once V2 is down this many dollars on the (Central) day
V2_START_BALANCE = 60.0          # simulated bankroll (about Pulse's real balance on 25 Oct)

V2_MAX_SECONDS_LEFT = 240
V2_MIN_SECONDS_LEFT = 20
V2_MIN_PRICE = 0.90
V2_MAX_PRICE = 0.98
V2_MIN_SIGMA = 1.8               # or the red star
V2_MIN_EDGE = 0.02               # win% - price - fee per share must be at least this
V2_MAX_SPREAD = 0.03

V2_RISK_FRACTION = 0.05
V2_GAP_LOSS = 0.35               # assumed worst loss per share when sizing
V2_MAX_SHARES = 40
V2_ROUND_CAP_FRACTION = 0.50

V2_BENCH_LOSSES = 2
V2_BENCH_HOURS = 24

V2_EXIT_SIGMA = 0.17             # 26 Oct (Matthew's rules for every bot): sell the moment it reads RED 3 ...
V2_EXIT_CONFIRM_SECONDS = 0      # ... no wait
V2_GAP_STOP = 0.25               # (paper only) sell at once if the held side is this far under the entry price
V2_STOP_FRACTION = 0.80          # live: sell instantly at 80% of entry (a 20% loss), like Pulse

V2_DAILY_STOP_FRACTION = 0.15

CT = timezone(timedelta(hours=-5))
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pulse_v2_state.json")

_s = {"balance": V2_START_BALANCE, "day": None, "day_start": V2_START_BALANCE,
      "n": 0, "wins": 0, "pnl": 0.0, "losses_by_coin": {}, "bench_until": {}}
_open = {}          # ticker -> paper position
_tried = set()      # tickers already entered once (one bet per market)
_buying = set()     # live buy in flight
_aftermath = {}     # ticker -> {"side": "bid"/"ask", "exit_value": x, "entry": y, "count": c, "who": "V2"/"LIVE", "closed_at": t}


def _tag():
    return "[V2-PAPER]" if V2_PAPER else "[V2]"


def owned() -> set:
    """Tickers V2 holds or is buying (live). Pulse's entries, adopter and backstop leave these alone."""
    return set() if V2_PAPER else (set(_open) | set(_buying))


def _load():
    try:
        if os.path.exists(_STATE_FILE):
            _s.update(json.load(open(_STATE_FILE)))
    except Exception as e:
        print(f"[V2-PAPER] couldn't load saved paper state ({e}) -- starting fresh at ${V2_START_BALANCE:.2f}.")


def _save():
    try:
        json.dump(_s, open(_STATE_FILE, "w"))
    except Exception:
        pass


_load()


def _fee_per_share(p: float) -> float:
    return math.ceil(0.07 * p * (1 - p) * 100 - 1e-9) / 100


def _fee(count: float, p: float) -> float:
    return math.ceil(0.07 * count * p * (1 - p) * 100 - 1e-9) / 100


def _dollars(x):
    if x is None:
        return None
    x = float(x)
    return x / 100 if x > 1.0 else x


def _cost_and_value(n: dict, side_yes: bool):
    """(cost to buy our side, value if we sold our side now) from the board's asks."""
    ya, na = _dollars(n.get("yes_ask")), _dollars(n.get("no_ask"))
    if ya is None or na is None:
        return None, None
    return (ya, 1 - na) if side_yes else (na, 1 - ya)


def _round_of(ticker: str) -> str:
    try:
        return ticker.split("-")[1]
    except Exception:
        return ticker


def _roll_day():
    today = datetime.now(CT).strftime("%Y-%m-%d")
    if _s["day"] != today:
        _s["day"] = today
        _s["day_start"] = _s["balance"] + sum(p["entry"] * p["count"] + p["fee_in"] for p in _open.values())


def _benched(coin: str) -> bool:
    until = _s["bench_until"].get(coin, 0)
    return time.time() < until


def _record_loss(coin: str):
    now = time.time()
    lst = [t for t in _s["losses_by_coin"].get(coin, []) if now - t < 24 * 3600] + [now]
    _s["losses_by_coin"][coin] = lst
    if len(lst) >= V2_BENCH_LOSSES:
        _s["bench_until"][coin] = now + V2_BENCH_HOURS * 3600
        _s["losses_by_coin"][coin] = []
        print(f"[V2-PAPER] BENCH {coin}: {V2_BENCH_LOSSES} losing bets inside 24h -- sits out {V2_BENCH_HOURS}h.")


def _close(ticker: str, pos: dict, value: float, why: str, settled: bool):
    fee_out = 0.0 if settled else _fee(pos["count"], value)
    pnl = (value - pos["entry"]) * pos["count"] - pos["fee_in"] - fee_out
    if pnl < 0:
        _record_loss(pos["coin"])
    if V2_PAPER:
        _s["balance"] += pos["entry"] * pos["count"] + pos["fee_in"] + pnl   # money back + result
        _s["n"] += 1
        _s["wins"] += 1 if pnl > 0 else 0
        _s["pnl"] += pnl
        print(f"[V2-PAPER] EXIT {ticker} ({pos['coin']}): {why} -- {pos['count']} @ ${pos['entry']:.2f} -> ${value:.2f}, "
              f"pnl ${pnl:+.2f} | paper balance ${_s['balance']:.2f} | trial: {_s['n']} bets, {_s['wins']} wins, ${_s['pnl']:+.2f}")
    else:
        _live_roll_day()
        _s["ln"] = _s.get("ln", 0) + 1
        _s["lw"] = _s.get("lw", 0) + (1 if pnl > 0 else 0)
        _s["lpnl"] = _s.get("lpnl", 0.0) + pnl
        _s["lday_pnl"] = _s.get("lday_pnl", 0.0) + pnl
        print(f"[V2] EXIT {ticker} ({pos['coin']}): {why} -- {pos['count']:g} @ ${pos['entry']:.2f} -> ${value:.2f}, "
              f"pnl ${pnl:+.2f} | live V2: {_s['ln']} bets, {_s['lw']} wins, ${_s['lpnl']:+.2f} (today ${_s['lday_pnl']:+.2f})")
    _open.pop(ticker, None)
    if not settled:
        _aftermath[ticker] = {"side": pos["side"], "exit_value": value, "entry": pos["entry"], "count": pos["count"],
                              "who": "V2", "closed_at": time.time()}
    _save()


def _live_roll_day():
    today = datetime.now(CT).strftime("%Y-%m-%d")
    if _s.get("lday") != today:
        _s["lday"], _s["lday_pnl"] = today, 0.0


def _live_value(client, ticker, side_yes):
    """What selling our side gets right now, straight from Kalshi. No buyers -> 0 (so the stop still fires)."""
    try:
        mk = client.get_market(ticker).get("market", {})
        ya, yb = _dollars(mk.get("yes_ask_dollars")), _dollars(mk.get("yes_bid_dollars"))
    except Exception:
        return None
    mine = yb if side_yes else (None if ya is None else 1 - ya)
    return 0.0 if (mine is None or mine <= 0) else mine


def _live_buy(client, ticker, side_yes, cost):
    """IOC buy of V2_LIVE_SHARES at `cost`. Re-reads the price first: any drop below the aimed price -> no buy.
    Returns (filled, avg_entry) or (0, None)."""
    import uuid
    try:
        mk = client.get_market(ticker).get("market", {})
        ya, yb = _dollars(mk.get("yes_ask_dollars")), _dollars(mk.get("yes_bid_dollars"))
        now_cost = ya if side_yes else (None if yb is None else 1 - yb)
        if now_cost is None or now_cost < cost - 1e-9 or now_cost < V2_MIN_PRICE - 1e-9 or now_cost > V2_MAX_PRICE + 1e-9:
            print(f"[V2] {ticker}: price moved before the buy ({cost:.2f} -> {'?' if now_cost is None else f'{now_cost:.2f}'}) -- skipping.")
            return 0, None
    except Exception:
        return 0, None
    yes_price = cost if side_yes else round(1 - cost, 2)
    try:
        client.create_order(ticker=ticker, client_order_id=f"pulse-v2-{uuid.uuid4()}",
                            side="bid" if side_yes else "ask", count=str(V2_LIVE_SHARES),
                            price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
    except Exception as e:
        print(f"[V2] {ticker}: buy order failed ({e}).")
        return 0, None
    for _ in range(4):
        try:
            held, exposure = flip_star._held_count(client, ticker)
        except Exception:
            held, exposure = 0.0, 0.0
        if abs(held) > 1e-9:
            return abs(held), (exposure / abs(held) if exposure > 0 else cost)
        time.sleep(0.4)
    return 0, None


def track_live_exit(ticker: str, position: dict, exit_value: float):
    """Called by bot.py after a LIVE Pulse early exit: follow the market to settlement and log what holding would have done."""
    try:
        _aftermath[ticker] = {"side": position.get("side"), "exit_value": float(exit_value),
                              "entry": float(position.get("entry_price", 0)), "count": float(position.get("count", 0)),
                              "who": "LIVE", "closed_at": time.time()}
    except Exception:
        pass


def _check_aftermath(client):
    now = time.time()
    for ticker, a in list(_aftermath.items()):
        if now - a.get("checked", 0) < 30:
            continue
        a["checked"] = now
        try:
            mk = client.get_market(ticker).get("market", {})
        except Exception:
            continue
        res = mk.get("result")
        if res in ("yes", "no"):
            won = (a["side"] == "bid") == (res == "yes")
            held_val = 1.0 if won else 0.0
            diff = (held_val - a["exit_value"]) * a["count"]
            verdict = "the sell SAVED" if diff < 0 else "the sell COST"
            print(f"[V2-AFTERMATH] {a['who']} {ticker}: sold @ ${a['exit_value']:.2f} (entry ${a['entry']:.2f}, {a['count']:g} sh); "
                  f"settled {res.upper()} -> held would have {'WON' if won else 'LOST'}; {verdict} ${abs(diff):.2f}")
            _aftermath.pop(ticker, None)
        elif now - a["closed_at"] > 3600:
            _aftermath.pop(ticker, None)


def _manage(client, b: dict):
    now = time.time()
    for ticker, pos in list(_open.items()):
        n = b.get(ticker)
        if n is None or n.get("tl") is None or n["tl"] <= 0:
            # window over (or board dropped it): wait for Kalshi's result
            if now - pos.get("res_checked", 0) < 20:
                continue
            pos["res_checked"] = now
            try:
                mk = client.get_market(ticker).get("market", {})
            except Exception:
                continue
            res = mk.get("result")
            if res in ("yes", "no"):
                won = (pos["side"] == "bid") == (res == "yes")
                _close(ticker, pos, 1.0 if won else 0.0, f"settled {res.upper()} ({'won' if won else 'lost'})", True)
            elif now - pos["t"] > 3600:
                print(f"[V2-PAPER] {ticker}: no result after an hour -- dropping the paper position.")
                _s["balance"] += pos["entry"] * pos["count"] + pos["fee_in"]
                _open.pop(ticker, None)
            continue
        side_yes = pos["side"] == "bid"
        _, value = _cost_and_value(n, side_yes)
        if not V2_PAPER:
            _manage_live(client, ticker, pos, n, side_yes, value, now)
            continue
        if value is None:
            continue
        # 1) gap stop -- instant
        if value <= pos["entry"] - V2_GAP_STOP + 1e-9:
            _close(ticker, pos, value, f"gap_stop (our side {value*100:.0f}c, {V2_GAP_STOP*100:.0f}c+ under entry)", False)
            continue
        # 2) sigma exit for OUR side, confirmed
        zs = n["zs"] if n.get("side") == ("yes" if side_yes else "no") else (-n["zs"] if n.get("zs") is not None else None)
        if zs is not None and zs <= V2_EXIT_SIGMA + 1e-9:
            since = pos.setdefault("red_since", now)
            if now - since >= V2_EXIT_CONFIRM_SECONDS:
                _close(ticker, pos, value, f"sigma_exit (To flip {zs:.2f} for {now - since:.0f}s)", False)
            continue
        pos.pop("red_since", None)
        # 3) otherwise hold to settlement


_place_exit = [None]


def _manage_live(client, ticker, pos, n, side_yes, value, now):
    if value is None or value <= 0:
        value = _live_value(client, ticker, side_yes)        # board has no price for our side -> ask Kalshi
        if value is None:
            return
    zs = n["zs"] if n.get("side") == ("yes" if side_yes else "no") else (-n["zs"] if n.get("zs") is not None else None)
    why = None
    if value <= pos["entry"] * V2_STOP_FRACTION + 1e-9:
        why = f"stop_80pct (our side {value*100:.0f}c <= 80% of entry ${pos['entry']:.2f})"
    elif zs is not None and zs <= V2_EXIT_SIGMA + 1e-9:
        why = f"red3 (To flip {zs:.2f} sigma <= {V2_EXIT_SIGMA}, our side {value*100:.0f}c)"
    if not why:
        return
    try:
        r = _place_exit[0](client, ticker, "ask" if side_yes else "bid", pos["count"], dry_run=False, entry_price=pos["entry"])
    except Exception as e:
        print(f"[V2] {ticker}: sell failed ({e}) -- retrying next pass.")
        return
    if isinstance(r, dict) and r.get("waiting"):
        return
    if isinstance(r, dict) and r.get("skipped"):
        print(f"[V2] {ticker}: Kalshi shows nothing left to sell -- dropping it.")
        _open.pop(ticker, None)
        return
    if isinstance(r, dict) and (r.get("_sold") or 0) > 1e-9:
        _close(ticker, pos, r.get("_real_exit_px", value), why, False)


def run(client, place_exit=None):
    """One pass: manage positions, follow aftermaths, look for new entries. Paper mode never sends orders;
    live mode buys IOC (V2_LIVE_SHARES) and sells through Pulse's place_exit."""
    if not V2_ENABLED:
        return
    if not V2_PAPER:
        if place_exit is None:
            return
        _place_exit[0] = place_exit
    b = flip_star.board()
    _check_aftermath(client)
    if not b:
        return
    _manage(client, b)
    if V2_PAPER:
        _roll_day()
        equity = _s["balance"] + sum(p["entry"] * p["count"] + p["fee_in"] for p in _open.values())
        if equity <= _s["day_start"] * (1 - V2_DAILY_STOP_FRACTION):
            return   # daily stop hit -- manage only
    else:
        _live_roll_day()
        if _s.get("lday_pnl", 0.0) <= -V2_LIVE_DAILY_STOP + 1e-9:
            return   # live daily stop hit -- manage only
        if len(_open) >= V2_LIVE_MAX_OPEN:
            return
    for ticker, n in b.items():
        if ticker in _open or ticker in _tried:
            continue
        if n.get("tl") is None or not (V2_MIN_SECONDS_LEFT <= n["tl"] <= V2_MAX_SECONDS_LEFT):
            continue
        if _benched(n.get("coin")):
            continue
        if n.get("zs") is None or not (n.get("lock") or n["zs"] >= V2_MIN_SIGMA):
            continue
        if n.get("spread") is not None and n["spread"] > V2_MAX_SPREAD + 1e-9:
            continue
        side_yes = n["side"] == "yes"
        cost, _ = _cost_and_value(n, side_yes)
        if cost is None or not (V2_MIN_PRICE <= cost <= V2_MAX_PRICE):
            continue
        edge = n["p"] - cost - _fee_per_share(cost)
        if edge < V2_MIN_EDGE - 1e-9:
            continue
        if not V2_PAPER:
            _enter_live(client, ticker, n, side_yes, cost, edge)
            if len(_open) >= V2_LIVE_MAX_OPEN:
                return
            continue
        shares = int(min(V2_MAX_SHARES, (_s["balance"] * V2_RISK_FRACTION) // V2_GAP_LOSS))
        rnd = _round_of(ticker)
        in_round = sum(p["entry"] * p["count"] for t, p in _open.items() if _round_of(t) == rnd)
        room = _s["balance"] * V2_ROUND_CAP_FRACTION - in_round
        shares = int(min(shares, room // cost, (_s["balance"] - 0.05) // cost))
        if shares < 1:
            continue
        fee_in = _fee(shares, cost)
        _s["balance"] -= cost * shares + fee_in
        _open[ticker] = {"coin": n.get("coin"), "side": "bid" if side_yes else "ask", "entry": cost, "count": shares,
                         "fee_in": fee_in, "t": time.time()}
        _tried.add(ticker)
        print(f"[V2-PAPER] ENTRY {ticker} ({n.get('coin')}): {n['side'].upper()} {shares} @ ${cost:.2f} (simulated) -- "
              f"win {n['p']*100:.1f}%, edge {edge*100:+.1f}c/share, To flip {n['zs']:.1f} sigma"
              f"{' RED STAR' if n.get('lock') else ''}, {int(n['tl'])}s left | paper balance ${_s['balance']:.2f}")
        _save()


_busy_fn = [lambda: set()]   # bot.py sets this to "tickers live Pulse holds / is buying"


def _enter_live(client, ticker, n, side_yes, cost, edge):
    if ticker in _busy_fn[0]() or ticker in flip_star.pending_tickers():
        return            # live Pulse is in this market -- one bot per market
    try:
        held, _ = flip_star._held_count(client, ticker)
        if abs(held) > 1e-9:
            return        # something (Pulse, a manual bet) already holds it
    except Exception:
        return
    _tried.add(ticker)
    _buying.add(ticker)
    try:
        count, px = _live_buy(client, ticker, side_yes, cost)
    finally:
        _buying.discard(ticker)
    if count <= 0:
        print(f"[V2] {ticker}: nothing filled at ${cost:.2f}.")
        return
    _open[ticker] = {"coin": n.get("coin"), "side": "bid" if side_yes else "ask", "entry": px, "count": count,
                     "fee_in": _fee(count, px), "t": time.time()}
    print(f"[V2] ENTRY {ticker} ({n.get('coin')}): {n['side'].upper()} {count:g} @ ${px:.2f} -- win {n['p']*100:.1f}%, "
          f"edge {edge*100:+.1f}c/share, To flip {n['zs']:.1f} sigma{' RED STAR' if n.get('lock') else ''}, {int(n['tl'])}s left")
    _save()
