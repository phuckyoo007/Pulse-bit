"""
CLOSER strategies (6 Nov 04:37 CT, Matt).

  RISING  -- LIVE (real money). Last 2:00 -> 1:00 of a 15-minute crypto round: buy RISING_SHARES of whichever side is above
             50c (below 97c) and has risen over the last ~20-45 seconds; hold to the end. One bet per coin per round.
             Paper result before going live (6 Nov 00:40-04:46 CT): 60 bets, 53 won, +$4.00 at 2 shares.
             Turns itself off for the day once its real bets have lost RISING_MAX_DAILY_LOSS ($2) in total.
  FLIPS   -- PAPER. 3:00 -> 2:00 left: board's pick at 70-95c, flipped <= 2 times, momentum not pushing hard its way.
  SWING   -- PAPER. Matt's "jump on, jump off": with 5:00+ left, buy a side at 55-70c right after it dropped 10c+ from its
             high of the last minute; sell the moment it can be sold 20c higher; otherwise hold to the end. May re-enter.
             Backtest (45 rounds, fees in): +9c/share on the half of the data not used to pick it -- thin, so paper first.

Paper log lines: "[CLOSER-PAPER] <RULE> BUY/SELL/RESULT ...". Live lines: "[RISING] ENTRY/FILLED/OFF ...".
"""
import math
import time
import uuid

import requests
from datetime import datetime, timedelta, timezone

import flip_star
import surge

CLOSER_ENABLED = True
RISING_LIVE = True   # 7 Nov 23:12 CT (Matt): real RISING bets back on (BTC + XRP, 2 shares, -$2/day stop)
RISING_SHARES = 2   # 8 Nov 11:41 CT (Matt): back to 2 shares (temporary) while the +$1/-$1 exit is checked
RISING_MAX_DAILY_LOSS = 2.00
RISING_TP_SL = 1.00   # 8 Nov 11:40 CT (Matt, temporary): exit RISING bets at +$1.00 profit or -$1.00 loss (0 = hold to end)
RULES = {
    "FLIPS":  {"shares": 5, "from": 180, "until": 120},
    "RISING": {"shares": 2, "from": 900, "until": 30},   # 7 Nov 23:13 CT (Matt): whole round (was 2:00 -> 1:00 left)
    "SWING":  {"shares": 2, "from": 900, "until": 300},
}
FLIPS_MIN_PRICE, FLIPS_MAX_PRICE, FLIPS_MAX_FLIPS, FLIPS_MAX_MOM_WITH = 0.70, 0.95, 2, 0.30
RISING_MIN_PRICE, RISING_MAX_PRICE = 0.50, 0.97
RISING_LOOKBACK = (20, 45)
EARLY_MAX_PRICE, EARLY_SECONDS = 0.80, 300   # 7 Nov 23:07 CT (Matt): nothing above 80c unless in the last 5 minutes
SWING_MIN_PRICE, SWING_MAX_PRICE, SWING_DIP, SWING_TAKE = 0.55, 0.70, 0.10, 0.20

_hist = {}     # ticker -> [(t, yes_ask, no_ask)]
_paper = {}    # (rule, ticker) -> bet ; SWING may hold several per ticker over a round (key gets a counter)
_tally = {}
MARKETS_BOARD = "https://markets-board-production.up.railway.app/api/state"   # commodities + currencies (paper only)
FX = {"EURUSD", "GBPUSD", "USDJPY"}
COMMODITIES = {"GOLD", "SILVER", "OIL", "BRENT", "NATGAS", "COPPER", "PLATINUM", "PALLADIUM"}
_mb = {"data": {}, "at": 0.0}


def _markets_board():
    """ticker -> board numbers (+ 'cat': CMD / FX) for the 15-minute commodity and currency markets (cached 1s)."""
    now = time.time()
    if now - _mb["at"] < 1.0:
        return _mb["data"]
    _mb["at"] = now
    out = {}
    try:
        st = requests.get(MARKETS_BOARD, timeout=4, headers={"Cache-Control": "no-store"}).json()
        age = max(0.0, now - float(st.get("now", now)))
        if age <= flip_star.FLIP_STALE_SECONDS:
            for m in st.get("markets", []):
                if m.get("kind") != "15m":
                    continue
                n = flip_star._side_numbers(m, age, st.get("coins") or {})
                if not n or not n.get("ticker"):
                    continue
                c = str(n.get("coin") or "").upper()
                n["cat"] = "FX" if c in FX else ("CMD" if c in COMMODITIES else None)
                if n["cat"]:
                    out[n["ticker"]] = n
    except Exception:
        pass
    _mb["data"] = out
    return out
_live_done = {}    # ticker -> time (one live RISING bet per coin per round)
_live_day = {"day": None, "pnl": 0.0, "off": False}


def _fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def _ct_day():
    return (datetime.now(timezone.utc) - timedelta(hours=6)).date()


def record(position, pnl) -> None:
    """bot.py calls this for every closed bet; tallies live RISING P/L and turns RISING off at -$2 for the day."""
    try:
        if not isinstance(position, dict) or not position.get("rising"):
            return
        if _live_day["day"] != _ct_day():
            _live_day.update(day=_ct_day(), pnl=0.0, off=False)
        _live_day["pnl"] += float(pnl)
        print(f"[RISING] bet closed {pnl:+.2f} -- today {_live_day['pnl']:+.2f} (stops at -${RISING_MAX_DAILY_LOSS:.2f}).")
        if not _live_day["off"] and _live_day["pnl"] <= -RISING_MAX_DAILY_LOSS + 1e-9:
            _live_day["off"] = True
            print(f"[RISING] OFF for today -- real RISING bets are down ${-_live_day['pnl']:.2f}.")
    except Exception:
        pass


def _base(rule):
    return rule.split("-")[0]


def _paper_buy(rule, key, ticker, n, side, px, tl, why, now):
    _paper[key] = {"rule": rule, "ticker": ticker, "coin": n.get("coin"), "side": side, "px": float(px), "close_at": now + tl}
    print(f"[CLOSER-PAPER] {rule} BUY {ticker} ({n.get('coin')}): {side.upper()} @ {px*100:.0f}c, {int(tl)}s left, {why} "
          f"-- {RULES[_base(rule)]['shares']} shares (paper)")


def _score(rule, bet, pnl, won, how):
    t = _tally.setdefault(rule, {"n": 0, "w": 0, "pnl": 0.0})
    t["n"] += 1
    t["w"] += won
    t["pnl"] += pnl
    print(f"[CLOSER-PAPER] {rule} {how} {bet['ticker']} ({bet['coin']}): {'WON' if won else 'LOST'} {pnl:+.2f} -- {rule} "
          f"paper total {t['n']} bets, {t['w']} won, {t['pnl']:+.2f}")


def _live_rising(client, state, excluded, state_lock, dry_run, ensure_funds, ticker, n, side, px, tl, why):
    if _live_day["day"] != _ct_day():
        _live_day.update(day=_ct_day(), pnl=0.0, off=False)
    if _live_day["off"] or flip_star.in_pause():
        return
    if flip_star.not_allowed_to_buy(n.get("coin"), ticker):
        return   # 6 Nov 04:53 CT (Matt): live bets on Bitcoin only (paper tests still watch every coin)
    if ticker in _live_done or ticker in state or ticker in excluded or ticker in flip_star.pending_tickers() \
            or ticker in flip_star.external_owned[0]():
        return
    _live_done[ticker] = time.time()
    try:
        held, _ = flip_star._held_count(client, ticker)
        if held != 0:
            return
        mk = client.get_market(ticker).get("market", {})
        ya, yb = float(mk.get("yes_ask_dollars")), float(mk.get("yes_bid_dollars"))
        if ya - yb > flip_star.FLIP_MAX_SPREAD + 1e-9:
            print(f"[RISING] {ticker}: spread {(ya-yb)*100:.0f}c too wide -- skipping.")
            return
        cost = ya if side == "yes" else 1 - yb
        if not (RISING_MIN_PRICE < cost < RISING_MAX_PRICE):
            return
        if tl > EARLY_SECONDS and cost > EARLY_MAX_PRICE + 1e-9:
            return   # 7 Nov (Matt): nothing above 80c unless in the last 5 minutes
        limit = min(RISING_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
    except Exception as e:
        print(f"[RISING] {ticker}: couldn't read the live quote ({e}).")
        return
    print(f"[RISING] ENTRY {ticker} ({n.get('coin')}): {side.upper()} rising ({why}), {int(tl)}s left -- "
          f"{'DRY-RUN ' if dry_run else ''}buying {RISING_SHARES} up to ${limit:.2f}")
    if dry_run:
        return
    bside = "bid" if side == "yes" else "ask"
    try:
        ensure_funds(client, ticker, limit * RISING_SHARES)
        yes_price = limit if bside == "bid" else round(1 - limit, 2)
        client.create_order(ticker=ticker, client_order_id=f"pulse-surge-rising-{uuid.uuid4()}", side=bside,
                            count=str(RISING_SHARES), price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
    except Exception as e:
        print(f"[RISING] {ticker}: entry order failed ({e}).")
        if "market_closed" in str(e) or "market_not_found" in str(e):
            excluded.add(ticker)
        return
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
        print(f"[RISING] {ticker}: nothing filled at ${limit:.2f} (price moved).")
        return
    entry_price = exposure / count if exposure > 0 else cost
    with state_lock:
        state[ticker] = {"count": count, "entry_price": entry_price, "side": bside, "dry_run": False,
                         "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n.get("coin"),
                         "flip_star": False, "surge": True, "rising": True}
    print(f"[RISING] FILLED {ticker}: {count:g} {side.upper()} @ ${entry_price:.2f} -- holding to the end.")


def run(client, state=None, excluded=None, state_lock=None, dry_run=False, ensure_funds=None) -> None:
    if not CLOSER_ENABLED:
        return
    now = time.time()
    try:
        b = flip_star.board()
    except Exception:
        b = {}
    _scan(b, "", True, now, client, state, excluded, state_lock, dry_run, ensure_funds)
    try:
        mb = _markets_board()
    except Exception:
        mb = {}
    for cat in ("CMD", "FX"):
        _scan({t: n for t, n in mb.items() if n.get("cat") == cat}, "-" + cat, False, now, client, state, excluded,
              state_lock, dry_run, ensure_funds)
    _settle(client, now)


def _scan(b, tag, live, now, client, state, excluded, state_lock, dry_run, ensure_funds):
    for ticker, n in (b or {}).items():
        if "15M" not in ticker.split("-")[0].upper() or n.get("tl") is None:
            continue
        ya, na, tl = n.get("yes_ask"), n.get("no_ask"), n["tl"]
        h = _hist.setdefault(ticker, [])
        h.append((now, ya, na))
        while h and now - h[0][0] > 90:
            h.pop(0)
        # FLIPS (paper)
        r = RULES["FLIPS"]
        if not tag and ("FLIPS", ticker) not in _paper and r["until"] <= tl <= r["from"]:
            side = n.get("side")
            px = ya if side == "yes" else na
            try:
                m = surge.momentum_for(ticker, side)
            except Exception:
                m = None
            if (px is not None and FLIPS_MIN_PRICE <= px <= FLIPS_MAX_PRICE and n.get("flips") is not None
                    and n["flips"] <= FLIPS_MAX_FLIPS and m is not None and m < FLIPS_MAX_MOM_WITH):
                _paper_buy("FLIPS", ("FLIPS", ticker), ticker, n, side, px, tl, f"{n['flips']} flips, momentum {m*100:+.0f}", now)
        # RISING (paper log + LIVE)
        r = RULES["RISING"]
        if ("RISING" + tag, ticker) not in _paper and r["until"] <= tl <= r["from"]:
            old = [x for x in h if RISING_LOOKBACK[0] <= now - x[0] <= RISING_LOOKBACK[1]]
            if old:
                _, ya0, na0 = old[-1]
                for side, px, px0 in (("yes", ya, ya0), ("no", na, na0)):
                    if (px is not None and px0 is not None and RISING_MIN_PRICE < px < RISING_MAX_PRICE and px > px0 + 1e-9
                            and not (tl > EARLY_SECONDS and px > EARLY_MAX_PRICE + 1e-9)):
                        why = f"up from {px0*100:.0f}c ~{int(now - old[-1][0])}s ago"
                        _paper_buy("RISING" + tag, ("RISING" + tag, ticker), ticker, n, side, px, tl, why, now)
                        if live and RISING_LIVE and state is not None:
                            try:
                                _live_rising(client, state, excluded, state_lock, dry_run, ensure_funds, ticker, n, side,
                                             px, tl, why)
                            except Exception as e:
                                print(f"[RISING] {ticker}: live entry failed ({e}).")
                        break
        # SWING (paper): sell open swing bets at +20c, otherwise look for a new dip to buy
        r = RULES["SWING"]
        sw = "SWING" + tag
        mine = [k for k, bet in _paper.items() if bet["rule"] == sw and bet["ticker"] == ticker and not bet.get("done")]
        for k in mine:
            bet = _paper[k]
            other = na if bet["side"] == "yes" else ya
            bid = None if other is None else 1 - other
            if bid is not None and bid >= bet["px"] + SWING_TAKE - 1e-9:
                pnl = ((bid - bet["px"]) - _fee(bet["px"]) - _fee(bid)) * r["shares"]
                bet["done"] = True
                _score(sw, bet, pnl, pnl > 0, f"SELL @ {bid*100:.0f}c")
        mine = [k for k, bet in _paper.items() if bet["rule"] == sw and bet["ticker"] == ticker and not bet.get("done")]
        if not mine and r["until"] <= tl <= r["from"] and len(h) >= 2:
            for side, ix in (("yes", 1), ("no", 2)):
                px = ya if side == "yes" else na
                recent = [x[ix] for x in h if now - x[0] <= 60 and x[ix] is not None]
                if px is None or not recent or not (SWING_MIN_PRICE <= px <= SWING_MAX_PRICE):
                    continue
                if max(recent) - px >= SWING_DIP - 1e-9:
                    key = (sw, ticker, int(now))
                    _paper_buy(sw, key, ticker, n, side, px, tl, f"dropped from {max(recent)*100:.0f}c in the last minute", now)
                    break


def _settle(client, now):
    # settle paper bets with Kalshi's real result
    for key, bet in list(_paper.items()):
        if bet.get("done") or now < bet["close_at"] + 60:
            continue
        try:
            res = client.get_market(bet["ticker"]).get("market", {}).get("result")
        except Exception:
            continue
        if res not in ("yes", "no"):
            if now > bet["close_at"] + 1800:
                bet["done"] = True
            continue
        won = res == bet["side"]
        pnl = ((1 - bet["px"] if won else -bet["px"]) - _fee(bet["px"])) * RULES[_base(bet["rule"])]["shares"]
        bet["done"] = True
        _score(bet["rule"], bet, pnl, won, "RESULT")
    for key in [k for k, bet in _paper.items() if bet.get("done") and now > bet["close_at"] + 3600]:
        _paper.pop(key, None)
    for t in [t for t, h in _hist.items() if h and now - h[-1][0] > 600]:
        _hist.pop(t, None)
    for t in [t for t, at in _live_done.items() if now - at > 1800]:
        _live_done.pop(t, None)
