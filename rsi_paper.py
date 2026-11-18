"""
RSI-PAPER (16 Nov 14:07 CT, Matt pasted a "Common 15-Minute Technical Strategy (RSI Momentum)"):
  * Buy YES when Coinbase's 14-period 1-minute RSI crosses ABOVE 60 and the 14-period 5-minute RSI is above 50.
  * Buy NO when the 1-minute RSI crosses BELOW 40 and the 5-minute RSI is below 50.
  * Exit when the 1-minute RSI reverts back through 50 before the window ends; otherwise hold to settlement.
PAPER ONLY.  Coins with a Coinbase USD market that the live bots trade: BTC, SOL, XRP (DOGE / ZEC are off the live list).
Buys at the real Kalshi ask, sells at the real bid, Kalshi fees both ways.  One ride per coin per 15-minute round, ~$2 a
ride, only while 60s+ are left and the side costs 15-90c (skips longshots and 91c+ prices the RSI can't improve on).
Logged as [RSI-PAPER] with running totals.

Matt (14:07 CT): "incorporate this and develop a couple new strategies".  Three paper versions run side by side:
  RSI-MOM  the pasted rule as written: 1-min RSI crosses 60/40 with the 5-min RSI agreeing, 15-90c, 60s+ left;
           sell when the 1-min RSI reverts through 50, else hold to the end.
  RSI-MID  same signal, but only in minutes 2-10 of the round and only near even money (35-65c) -- the article says the
           middle of the window has a different risk profile than the last minute, and a 50c contract doubles if right.
  RSI-FAV  our proven favourite-side idea + RSI as the confirmation: last 3 minutes, favourite 90-97c, buy only when the
           1-min RSI (55+/45-) AND the 5-min RSI (50+/50-) point the same way as the favourite; hold to the end,
           76% stop.  Research (Whelan, UCD) says favourites are the side that earns -- RSI should screen out the reversals.
"""
import math
import time

import requests

import flip_star

RSI_ENABLED = True
RSI_DOLLARS = 2.00
RSI_COINS = {"BTC": "BTC-USD", "SOL": "SOL-USD", "XRP": "XRP-USD"}
RSI_POLL = 20             # seconds between Coinbase candle pulls
RSI_MIN_SECONDS_LEFT = 60
RSI_MIN_PRICE = 0.15
RSI_MAX_PRICE = 0.90

_last = [0.0]
_rsi = {}        # coin -> {"r1": [prev, now], "r5": now, "t": time}
VARIANTS = [
    # 17 Nov 01:40Z: RSI-MOM OFF -- 7 rides, 2 won, -$7.01 since the settle fix (and losing before it).
    {"tag": "RSI-MOM", "off": True, "kind": "cross", "lo": 0.15, "hi": 0.90, "min_left": 60, "max_left": 900, "rsi_exit": True},
    {"tag": "RSI-MID", "off": True, "kind": "cross", "lo": 0.35, "hi": 0.65, "min_left": 300, "max_left": 780, "rsi_exit": True},
    # 17 Nov 10:45Z: RSI-MID-H = RSI-MID but HOLD TO THE END (no RSI-back-through-50 exit).  One change, to test whether
    # the early RSI exit helps or hurts: RSI-MID's exits sold at 25c/57c/58c, and near-even-money bets that are right pay $1.
    {"tag": "RSI-MID-H", "off": True, "kind": "cross", "lo": 0.35, "hi": 0.65, "min_left": 300, "max_left": 780, "rsi_exit": False},
    # 17 Nov 16:47Z: RSI-FAV OFF -- 29 rides, 23 won, -$3.65: small 90c+ wins, and each loss wipes out ~10 of them.
    {"tag": "RSI-FAV", "off": True, "kind": "fav", "lo": 0.90, "hi": 0.97, "min_left": 10, "max_left": 180, "rsi_exit": False,
     "stop": 0.76},
]
_pos = {}        # (tag, ticker) -> {"side", "cost", "count", "at", "coin", "tag"}
_done = set()
_tot = {v["tag"]: {"n": 0, "up": 0, "pnl": 0.0} for v in VARIANTS}


def _fee(p, n):
    return math.ceil(0.07 * p * (1 - p) * n * 100 - 1e-9) / 100


def _rsi14(closes):
    if len(closes) < 15:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-15:-1], closes[-14:]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / 14) / (losses / 14)
    return 100 - 100 / (1 + rs)


def _candles(product, gran):
    r = requests.get(f"https://api.exchange.coinbase.com/products/{product}/candles",
                     params={"granularity": gran}, timeout=5, headers={"User-Agent": "pulse-rsi-paper"})
    r.raise_for_status()
    rows = sorted(r.json(), key=lambda x: x[0])   # [time, low, high, open, close, volume]
    return [float(x[4]) for x in rows]


def _refresh():
    for coin, prod in RSI_COINS.items():
        try:
            r1 = _rsi14(_candles(prod, 60))
            r5 = _rsi14(_candles(prod, 300))
        except Exception as e:
            print(f"[RSI-PAPER] {coin}: Coinbase candles failed ({e}).")
            continue
        if r1 is None or r5 is None:
            continue
        old = _rsi.get(coin, {}).get("r1", [None, None])[1]
        _rsi[coin] = {"r1": [old, r1], "r5": r5, "t": time.time()}


def _close(key, p, value, why):
    ticker = key[1]
    T = _tot[p["tag"]]
    n = p["count"]
    pnl = (value - p["cost"]) * n - _fee(p["cost"], n) - (_fee(value, n) if 0 < value < 1 else 0)
    T["n"] += 1
    T["up"] += 1 if pnl > 0 else 0
    T["pnl"] += pnl
    print(f"[RSI-PAPER] {p['tag']} SELL {ticker} ({p['coin']}) {p['side'].upper()} {n} @ {value*100:.0f}c (bought {p['cost']*100:.0f}c, "
          f"{time.time() - p['at']:.0f}s): {why} -> {pnl:+.2f} | {p['tag']} total {T['n']} rides, {T['up']} up, {T['pnl']:+.2f}")
    _pos.pop(key, None)


def run(client=None) -> None:
    if not RSI_ENABLED:
        return
    now = time.time()
    if now - _last[0] < RSI_POLL:
        return
    _last[0] = now
    _refresh()
    b = flip_star.board() or {}
    for t, n in b.items():
        coin = n.get("coin")
        if coin not in RSI_COINS or "15M" not in t.split("-")[0].upper():
            continue
        ya, na, tl = n.get("yes_ask"), n.get("no_ask"), n.get("tl")
        if ya is None or na is None or tl is None:
            continue
        ya, na, tl = float(ya), float(na), float(tl)
        yb, nb = 1 - na, 1 - ya
        r = _rsi.get(coin)
        for v in VARIANTS:
            key = (v["tag"], t)
            p = _pos.get(key)
            if v.get("off") and not p:
                continue
            if p:
                bid = yb if p["side"] == "yes" else nb
                p["last_bid"] = bid
                if tl <= 3:   # settled: a side worth 50c+ in the last seconds wins $1
                    _close(key, p, 1.0 if bid >= 0.5 else 0.0, f"held to the end -- {'WON' if bid >= 0.5 else 'lost'}")
                elif v.get("stop") and bid <= p["cost"] * v["stop"] + 1e-9:
                    _close(key, p, bid, f"stop at {v['stop']*100:.0f}% of cost")
                elif v["rsi_exit"] and r and r["r1"][1] is not None and (
                        (p["side"] == "yes" and r["r1"][1] < 50) or (p["side"] == "no" and r["r1"][1] > 50)):
                    _close(key, p, bid, f"1-min RSI back through 50 ({r['r1'][1]:.0f}) -- out")
                continue
            if key in _done or not r or not (v["min_left"] <= tl <= v["max_left"]) or now - r["t"] > 60:
                continue
            prev, cur = r["r1"]
            side = None
            if v["kind"] == "cross":
                if prev is None:
                    continue
                if prev <= 60 < cur and r["r5"] > 50:
                    side = "yes"
                elif prev >= 40 > cur and r["r5"] < 50:
                    side = "no"
            else:   # fav
                fav = "yes" if ya >= na else "no"
                if fav == "yes" and cur >= 55 and r["r5"] > 50:
                    side = "yes"
                elif fav == "no" and cur <= 45 and r["r5"] < 50:
                    side = "no"
            if not side:
                continue
            ask = ya if side == "yes" else na
            if not (v["lo"] <= ask <= v["hi"]):
                continue
            count = max(1, int(RSI_DOLLARS / ask))
            _pos[key] = {"side": side, "cost": ask, "count": count, "at": now, "coin": coin, "tag": v["tag"]}
            _done.add(key)
            print(f"[RSI-PAPER] {v['tag']} BUY {t} ({coin}) {side.upper()} {count} @ {ask*100:.0f}c: 1-min RSI "
                  f"{'?' if prev is None else f'{prev:.0f}'} -> {cur:.0f}, 5-min RSI {r['r5']:.0f}, {tl:.0f}s left")
    # 16 Nov 16:35 CT fix: the board drops a market a few seconds before the close, so "gone" is NOT a loss --
    # settle it from the last price we saw (50c+ = that side won).  Earlier "counted as a loss" rides were wrong.
    for k in [k for k in _pos if k[1] not in b]:
        lb = _pos[k].get("last_bid", 0.0)
        _close(k, _pos[k], 1.0 if lb >= 0.5 else 0.0, f"round ended (last price {lb*100:.0f}c) -- {'WON' if lb >= 0.5 else 'lost'}")
