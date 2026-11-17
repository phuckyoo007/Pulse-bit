"""
TA-PAPER (16 Nov 17:55 CT, Matt pasted "EMA Pullback + RSI Momentum" and "Bollinger Band Mean Reversion").
On a 15-minute chart, both are DIRECTION calls for the next 15 minutes -- exactly what a Kalshi 15-minute up/down round
asks.  So:
  EMA-PB  trend filter: 20 EMA above the 50 EMA and both rising (mirror for down).  Trigger: the last 15-min candle dipped
          to/under the 20 EMA and CLOSED back above it, with the 14 RSI above 50  ->  buy YES ("up") on the new round.
          Mirror (20 EMA under 50 EMA and falling, candle poked above the 20 EMA and closed back under, RSI < 50) -> NO.
  BB-MR   20-period / 2-std Bollinger bands + 14 RSI.  A candle closed OUTSIDE the lower band with RSI < 30, and the next
          candle closed green back INSIDE the band  ->  buy YES.  Mirror at the upper band with RSI > 70 -> NO.
Entry: in the first 3 minutes of the new Kalshi round, at the real ask, only near even money (35-65c), ~$2 a ride.
Two exits per signal (the post asks for 1:2 risk/reward):
  -HOLD   hold to the end (a correct direction call pays $1)
  -1to2   sell at +30c above what we paid, or cut at -15c (1:2), else hold to the end.
BACKTEST (the post: "test over at least 100 historical setups"): on start-up, replays ~30 days of Coinbase 15-minute
candles for BTC, ETH, SOL, XRP: a correct call = the NEXT candle closes above (YES) / below (NO) its open, bought at 50c
plus the Kalshi fee.  Logged as [TA TEST].  Live paper rides are logged as [TA-PAPER].  PAPER ONLY.
"""
import math
import threading
import time

import requests

import flip_star

TA_ENABLED = True
TA_DOLLARS = 2.00
TA_COINS = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XRP": "XRP-USD"}
TA_ENTRY_WINDOW = 180          # only in the first 3 minutes of a round
TA_MIN_PRICE, TA_MAX_PRICE = 0.35, 0.65
TA_POLL = 30

_last = [0.0]
_sig = {}        # coin -> {"bucket": candle start, "EMA-PB": side|None, "BB-MR": side|None}
_pos = {}        # (tag, ticker) -> dict
_done = set()
TAGS = ("EMA-PB-HOLD", "EMA-PB-1to2", "BB-MR-HOLD", "BB-MR-1to2")
_tot = {t: {"n": 0, "up": 0, "pnl": 0.0} for t in TAGS}
_bt_started = [False]


def _fee(p, n):
    return math.ceil(0.07 * p * (1 - p) * n * 100 - 1e-9) / 100


def _get(product, gran, end=None):
    params = {"granularity": gran}
    if end is not None:
        params["end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end))
        params["start"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end - gran * 300))
    r = requests.get(f"https://api.exchange.coinbase.com/products/{product}/candles", params=params, timeout=8,
                     headers={"User-Agent": "pulse-ta-paper"})
    r.raise_for_status()
    return sorted(r.json(), key=lambda x: x[0])   # [time, low, high, open, close, volume]


def _ema(vals, n):
    k = 2 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def _rsi(closes, i, n=14):
    if i < n:
        return None
    g = l = 0.0
    for j in range(i - n + 1, i + 1):
        d = closes[j] - closes[j - 1]
        g += max(d, 0)
        l += max(-d, 0)
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def _signals(c):
    """c = candles [t, low, high, open, close, vol] oldest first.  Returns {i: {"EMA-PB": side, "BB-MR": side}} for each
    closed candle i whose signal applies to candle i+1."""
    closes = [x[4] for x in c]
    e20, e50 = _ema(closes, 20), _ema(closes, 50)
    out = {}
    for i in range(55, len(c)):
        s = {}
        r = _rsi(closes, i)
        lo, hi, cl = c[i][1], c[i][2], c[i][4]
        up = e20[i] > e50[i] and e20[i] > e20[i - 1] and e50[i] > e50[i - 1]
        dn = e20[i] < e50[i] and e20[i] < e20[i - 1] and e50[i] < e50[i - 1]
        if r is not None and up and lo <= e20[i] and cl > e20[i] and r > 50:
            s["EMA-PB"] = "yes"
        elif r is not None and dn and hi >= e20[i] and cl < e20[i] and r < 50:
            s["EMA-PB"] = "no"
        if i >= 21:
            w = closes[i - 20:i]          # bands as of the PREVIOUS candle (the one that pierced)
            m = sum(w) / 20
            sd = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
            lower, upper = m - 2 * sd, m + 2 * sd
            rp = _rsi(closes, i - 1)
            prev_cl = closes[i - 1]
            if rp is not None and prev_cl < lower and rp < 30 and cl > c[i][3] and cl > lower:
                s["BB-MR"] = "yes"
            elif rp is not None and prev_cl > upper and rp > 70 and cl < c[i][3] and cl < upper:
                s["BB-MR"] = "no"
        if s:
            out[i] = s
    return out


def _backtest():
    res = {}
    for coin, prod in TA_COINS.items():
        try:
            rows, end = [], time.time()
            for _ in range(10):          # 10 x 300 x 15 min ~= 31 days
                part = _get(prod, 900, end)
                if not part:
                    break
                rows = part + rows
                end = part[0][0]
                time.sleep(0.4)
            seen, c = set(), []
            for x in rows:
                if x[0] not in seen:
                    seen.add(x[0])
                    c.append(x)
            c.sort(key=lambda x: x[0])
            for i, s in _signals(c).items():
                if i + 1 >= len(c):
                    continue
                nxt = c[i + 1]
                for strat, side in s.items():
                    won = (nxt[4] > nxt[3]) if side == "yes" else (nxt[4] < nxt[3])
                    pnl = (0.50 if won else -0.50) - _fee(0.5, 1)
                    v = res.setdefault((strat, coin), [0, 0, 0.0])
                    v[0] += 1
                    v[1] += 1 if won else 0
                    v[2] += pnl
        except Exception as e:
            print(f"[TA TEST] {coin}: candle history failed ({e}).")
    print("[TA TEST] ===== EMA pullback + RSI, and Bollinger mean reversion, on ~30 days of Coinbase 15-min candles: "
          "does the signal call the NEXT 15 minutes' direction?  Bought at 50c + Kalshi fee, per contract =====")
    tot = {}
    for (strat, coin), (n, w, p) in sorted(res.items()):
        print(f"[TA TEST] {strat:7s} {coin}: {n} signals, {w / n * 100:.1f}% right, P/L {p:+.2f} per contract total "
              f"({p / n * 100:+.1f}c a bet)")
        t = tot.setdefault(strat, [0, 0, 0.0])
        t[0] += n
        t[1] += w
        t[2] += p
    for strat, (n, w, p) in tot.items():
        print(f"[TA TEST] {strat:7s} ALL: {n} signals, {w / n * 100:.1f}% right, {p / n * 100:+.1f}c a bet "
              f"(needs ~52% right to beat the fee)")
    print("[TA TEST] done.")


def _refresh():
    for coin, prod in TA_COINS.items():
        try:
            c = _get(prod, 900)
        except Exception as e:
            print(f"[TA-PAPER] {coin}: Coinbase candles failed ({e}).")
            continue
        if len(c) < 60:
            continue
        # the newest row may be the candle still forming -- use only closed candles
        if time.time() - c[-1][0] < 900:
            c = c[:-1]
        sig = _signals(c).get(len(c) - 1, {})
        _sig[coin] = {"bucket": c[-1][0] + 900, **sig}


def _close(key, p, value, why):
    T = _tot[p["tag"]]
    n = p["count"]
    pnl = (value - p["cost"]) * n - _fee(p["cost"], n) - (_fee(value, n) if 0 < value < 1 else 0)
    T["n"] += 1
    T["up"] += 1 if pnl > 0 else 0
    T["pnl"] += pnl
    print(f"[TA-PAPER] {p['tag']} SELL {key[1]} ({p['coin']}) {p['side'].upper()} {n} @ {value*100:.0f}c (bought "
          f"{p['cost']*100:.0f}c): {why} -> {pnl:+.2f} | {p['tag']} total {T['n']} rides, {T['up']} up, {T['pnl']:+.2f}")
    _pos.pop(key, None)


def run(client=None) -> None:
    if not TA_ENABLED:
        return
    if not _bt_started[0]:
        _bt_started[0] = True
        threading.Thread(target=_backtest, daemon=True).start()
    now = time.time()
    b = flip_star.board() or {}
    # exits every pass
    for key, p in list(_pos.items()):
        n = b.get(key[1])
        if not n:
            lb = p.get("last_bid", 0.0)
            _close(key, p, 1.0 if lb >= 0.5 else 0.0, f"round ended (last price {lb*100:.0f}c) -- {'WON' if lb >= 0.5 else 'lost'}")
            continue
        ya, na, tl = n.get("yes_ask"), n.get("no_ask"), n.get("tl")
        if ya is None or na is None or tl is None:
            continue
        bid = (1 - float(na)) if p["side"] == "yes" else (1 - float(ya))
        p["last_bid"] = bid
        if float(tl) <= 3:
            _close(key, p, 1.0 if bid >= 0.5 else 0.0, f"held to the end -- {'WON' if bid >= 0.5 else 'lost'}")
        elif p["tag"].endswith("1to2"):
            if bid >= p["cost"] + 0.30 - 1e-9:
                _close(key, p, bid, "hit +30c (2x the risk) -- cashing in")
            elif bid <= p["cost"] - 0.15 + 1e-9:
                _close(key, p, bid, "hit -15c stop (1x the risk)")
    if now - _last[0] < TA_POLL:
        return
    _last[0] = now
    _refresh()
    for t, n in b.items():
        coin = n.get("coin")
        if coin not in TA_COINS or "15M" not in t.split("-")[0].upper():
            continue
        s = _sig.get(coin)
        tl = n.get("tl")
        if not s or tl is None or float(tl) < 900 - TA_ENTRY_WINDOW:
            continue
        # 17 Nov 07:40Z: EMA-PB OFF -- went negative on paper (13 rides, 7 won, -$0.31) and the 30-day backtest says
        # it calls the direction only 49.6% of the time.
        for strat in ("BB-MR",):
            side = s.get(strat)
            if not side:
                continue
            ask = float(n["yes_ask"] if side == "yes" else n["no_ask"])
            if not (TA_MIN_PRICE <= ask <= TA_MAX_PRICE):
                continue
            # 17 Nov 01:40Z: the -15c/+30c exit went 0 for 4 (-$2.61) -- near 50c, normal wiggle hits the -15c stop.
            for tag in (strat + "-HOLD",):
                key = (tag, t)
                if key in _done:
                    continue
                _done.add(key)
                cnt = max(1, int(TA_DOLLARS / ask))
                _pos[key] = {"tag": tag, "side": side, "cost": ask, "count": cnt, "coin": coin}
                print(f"[TA-PAPER] {tag} BUY {t} ({coin}) {side.upper()} {cnt} @ {ask*100:.0f}c: {strat} signal on the "
                      f"last 15-min candle, {float(tl):.0f}s left")
