"""
Hour-of-day study (11 Nov 15:20 CT, Matt: "look at the trades over the last few weeks ... PnL based on each hour").
READ-ONLY -- never trades.

Two parts:

1. HISTORY (runs once, in a slow background thread so it can't crowd out live trading):
   for each of the seven coins, every settled 15-minute market from the last HS_DAYS days, pulls Kalshi's
   1-minute price candles and replays each strategy's entry rule on them (held to the end, HS_SHARES shares, after fees).
   Results are grouped by the Central-time hour the round ended in. Logged as [HOUR STUDY] lines.
   (Uses market prices only -- the edge board's win %/momentum weren't recorded that far back -- so "the board's pick"
   is approximated by "the side priced above 50c", and LATE60's momentum by "that side's price rose over the last minute".)

2. LIVE PAPER (keeps running): every pass it applies each strategy's rule to the edge board for all seven coins,
   records a paper bet, settles it from Kalshi's result, and keeps P/L per strategy per CT hour. Logged as
   [HOUR PAPER] lines, with a full table at the top of every hour.
"""
import json
import threading
import time
from datetime import datetime, timezone, timedelta

import flip_star

HS_ENABLED = True
HS_DAYS = 14
HS_SHARES = 2
HS_COINS = ["BTC", "XRP", "SOL", "DOGE", "BNB", "HYPE", "ZEC"]
HS_REQ_PER_SEC = 2.0

# (name, min secs left, max secs left, min price, max price, needs rising price)
RULES = [
    ("EARLY93", 240, 420, 0.93, 0.98, False),
    ("LATE80", 120, 240, 0.80, 0.93, False),
    ("LATE60", 120, 240, 0.60, 0.80, True),
    ("FLIP94", 10, 300, 0.94, 0.98, False),
    ("BTC92", 10, 120, 0.92, 0.98, False),
    # 12 Nov 14:53 CT: the tightened versions now used for real money
    ("EARLY96", 240, 420, 0.96, 0.98, False),
    ("BTC94", 10, 120, 0.94, 0.98, False),
    ("FLIP98", 10, 300, 0.96, 0.98, False),   # live paper also needs 98%+ win and <=2 flips (see run)
]


def _ct_hour(ts):
    # Central time (UTC-6 in November)
    return (datetime.fromtimestamp(ts, timezone.utc) - timedelta(hours=6)).hour


def _fee(p, n):
    import math
    return math.ceil(0.07 * p * (1 - p) * n * 100 - 1e-9) / 100


def _price(v):
    if v is None:
        return None
    try:
        v = float(v)
    except Exception:
        return None
    return v / 100 if v > 1.0 else v


def _close_of(c, key):
    d = c.get(key) or {}
    for k in ("close_dollars", "close"):
        if d.get(k) is not None:
            return _price(d.get(k))
    return None


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _table(title, agg):
    """agg: {(rule, hour): [n, won, pnl]}"""
    print(f"[HOUR STUDY] ===== {title} (P/L at {HS_SHARES} shares, held to the end; hour = Central time the round ended) =====")
    for name in [r[0] for r in RULES] + sorted({k[0] for k in agg} - {r[0] for r in RULES}):
        rows = [(h, agg.get((name, h))) for h in range(24)]
        tot_n = sum(r[0] for _, r in rows if r)
        tot_p = sum(r[2] for _, r in rows if r)
        tot_w = sum(r[1] for _, r in rows if r)
        print(f"[HOUR STUDY] {name}: {tot_n} bets, won {tot_w}, P/L ${tot_p:+.2f}")
        parts = []
        for h, r in rows:
            if r and r[0]:
                parts.append(f"{h:02d}h {r[2]:+.2f} ({r[1]}/{r[0]})")
        for i in range(0, len(parts), 6):
            print(f"[HOUR STUDY] {name}   " + "  |  ".join(parts[i:i + 6]))
    print("[HOUR STUDY] JSON " + json.dumps({f"{k[0]}|{k[1]}": [v[0], v[1], round(v[2], 2)] for k, v in agg.items()},
                                           separators=(",", ":")))


def _history(client):
    try:
        gap = 1.0 / HS_REQ_PER_SEC
        since = int(time.time() - HS_DAYS * 86400)
        markets = []
        for coin in HS_COINS:
            series = f"KX{coin}15M"
            cursor = None
            for _ in range(200):
                time.sleep(gap)
                try:
                    r = client.get_markets(status="settled", series_ticker=series, limit=1000, cursor=cursor,
                                           min_close_ts=since)
                except Exception as e:
                    print(f"[HOUR STUDY] {series}: market list failed ({e}) -- retrying in 30s.")
                    time.sleep(30)
                    continue
                for m in r.get("markets", []) or []:
                    ct = _ts(m.get("close_time"))
                    if ct and m.get("result") in ("yes", "no"):
                        markets.append((coin, series, m["ticker"], int(ct), m["result"]))
                cursor = r.get("cursor")
                if not cursor:
                    break
        print(f"[HOUR STUDY] history: {len(markets)} settled 15-minute rounds over {HS_DAYS} days -- replaying "
              f"(about {len(markets) / HS_REQ_PER_SEC / 60:.0f} min).")
        agg = {}
        days = {}
        done = 0
        for coin, series, ticker, close_ts, result in markets:
            time.sleep(gap)
            try:
                r = client._request("GET", f"/series/{series}/markets/{ticker}/candlesticks",
                                    params={"start_ts": close_ts - 960, "end_ts": close_ts, "period_interval": 1})
            except Exception as e:
                if "429" in str(e):
                    time.sleep(10)
                continue
            candles = []
            for c in r.get("candlesticks", []) or []:
                t = c.get("end_period_ts")
                ya, yb = _close_of(c, "yes_ask"), _close_of(c, "yes_bid")
                if t is None or ya is None or yb is None or ya <= 0 or yb <= 0:
                    continue
                candles.append((close_ts - int(t), ya, yb))
            candles.sort(key=lambda x: -x[0])   # most time left first
            _rounds.setdefault(close_ts, {})[coin] = (candles, result)
            hour = _ct_hour(close_ts)
            day = (datetime.fromtimestamp(close_ts, timezone.utc) - timedelta(hours=6)).strftime("%m-%d")
            for name, tl0, tl1, lo, hi, rising in RULES:
                prev = None
                for ci, (tl, ya, yb) in enumerate(candles):
                    side = "yes" if (ya + yb) / 2 >= 0.5 else "no"
                    cost = ya if side == "yes" else 1 - yb
                    mid = (ya + yb) / 2 if side == "yes" else 1 - (ya + yb) / 2
                    ok = tl0 <= tl <= tl1 and lo <= cost <= hi
                    if ok and rising:
                        ok = prev is not None and prev[0] == side and mid > prev[1] + 1e-9
                    prev = (side, mid)
                    if not ok:
                        continue
                    won = result == side
                    pnl = ((1.0 if won else 0.0) - cost) * HS_SHARES - _fee(cost, HS_SHARES)
                    # 13 Nov 03:23 CT (Matt): would a stop at 90/80/70/60/50/40/30/20% of entry have helped?
                    for frac in STOP_TEST:
                        s_pnl, hit = pnl, False
                        for t2, a2, b2 in candles[ci + 1:]:
                            bid = b2 if side == "yes" else 1 - a2
                            if bid <= cost * frac + 1e-9:
                                s_pnl = (bid - cost) * HS_SHARES - _fee(cost, HS_SHARES) - (_fee(bid, HS_SHARES) if bid > 0 else 0)
                                hit = True
                                break
                        st = _stops.setdefault((name, frac), [0, 0, 0.0, 0, 0.0])
                        st[0] += 1
                        st[2] += s_pnl
                        if hit:
                            st[1] += 1
                            st[3] += 1 if won else 0      # stopped out of a bet that would have WON
                            st[4] += s_pnl - pnl          # money the stop made (+) or cost (-) vs holding
                    a = agg.setdefault((name, hour), [0, 0, 0.0])
                    a[0] += 1
                    a[1] += 1 if won else 0
                    a[2] += pnl
                    d = days.setdefault((name, day), [0, 0, 0.0])
                    d[0] += 1
                    d[1] += 1 if won else 0
                    d[2] += pnl
                    break
            done += 1
            if done % 500 == 0:
                print(f"[HOUR STUDY] history: {done}/{len(markets)} rounds replayed.")
        _table(f"HISTORY, last {HS_DAYS} days, {done} rounds", agg)
        try:
            print(f"[STOP TEST] ===== stop at X% of what we paid vs holding to the end, last {HS_DAYS} days, "
                  f"{HS_SHARES} shares, after fees (1-minute prices: real stops fill a little worse) =====")
            for name, *_ in RULES:
                parts = []
                for frac in STOP_TEST:
                    st = _stops.get((name, frac))
                    if not st:
                        continue
                    lab = "no stop" if frac > 1 else f"{frac * 100:.0f}%"
                    parts.append(f"{lab}: ${st[2]:+.2f}" + ("" if frac > 1 else
                                 f" ({st[1]} stopped, {st[3]} of them would have won, vs hold {st[4]:+.2f})"))
                print(f"[STOP TEST] {name}: " + " | ".join(parts))
        except Exception as e:
            print(f"[STOP TEST] crashed ({e}).")
        try:
            _combo()
        except Exception as e:
            print(f"[COMBO] crashed ({e}).")
        try:
            _cheap()
        except Exception as e:
            print(f"[CHEAP] crashed ({e}).")
        try:
            _turn()
        except Exception as e:
            print(f"[TURN] crashed ({e}).")
        try:
            _pockets()
        except Exception as e:
            print(f"[POCKET] crashed ({e}).")
        for name, *_ in RULES:
            parts = [f"{k[1]} {v[2]:+.2f} ({v[1]}/{v[0]})" for k, v in sorted(days.items()) if k[0] == name]
            print(f"[HOUR STUDY] {name} by day: " + "  |  ".join(parts))
    except Exception as e:
        print(f"[HOUR STUDY] history crashed ({e}).")


# ---------------- COMBO: all coins together (12 Nov 15:10 CT, Matt) ----------------
# "Can't you use collective pieces of data to see which direction it's going?"
# For every round of the last HS_DAYS days, at a decision time (5:00 or 3:00 left) combine:
#   * this coin's own direction over the last 2 minutes (its YES price rising = heading UP),
#   * how many of the OTHER six coins moved the same way over those 2 minutes,
#   * whether Bitcoin agrees,
#   * where the other coins already stand (their YES prices above/below 50c).
# Then buy the predicted side at its real ask price (only 35-85c, where a win pays enough) and hold to the end.
# Logged as [COMBO] lines: bets, % won, average price paid, P/L at HS_SHARES shares after fees, and EDGE
# (% won minus average price -- positive means the combined read beat the market's own odds).
STOP_TEST = (1.01, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20)   # 1.01 = never triggers = hold to the end
_stops = {}    # (rule, frac) -> [bets, stopped, P/L, stopped-but-would-have-won, stop vs hold]
_rounds = {}   # close_ts -> {coin: (candles [(secs_left, yes_ask, yes_bid)], result)}


def _at(candles, tl):
    """(yes_ask, yes_bid) at the last candle with at least tl seconds left, within 60s."""
    best = None
    for t, ya, yb in candles:
        if tl <= t <= tl + 60:
            best = (ya, yb)
    return best


def _combo():
    variants = {}
    for tl in (300, 180):
        for close_ts, coins in _rounds.items():
            mids_now, moves = {}, {}
            for coin, (c, res) in coins.items():
                a, b = _at(c, tl), _at(c, tl + 120)
                if not a or not b:
                    continue
                mids_now[coin] = (a[0] + a[1]) / 2
                moves[coin] = mids_now[coin] - (b[0] + b[1]) / 2
            if len(moves) < 5:
                continue
            for coin, mv in moves.items():
                if abs(mv) < 0.02:
                    continue
                up = mv > 0
                others = [m for k, m in moves.items() if k != coin]
                agree = sum(1 for m in others if (m > 0.01 if up else m < -0.01))
                btc = moves.get("BTC")
                btc_ok = coin == "BTC" or (btc is not None and (btc > 0.01 if up else btc < -0.01))
                o_lvl = [v for k, v in mids_now.items() if k != coin]
                crowd_lvl = sum(1 for v in o_lvl if (v > 0.5 if up else v < 0.5))
                c, res = coins[coin]
                ya, yb = _at(c, tl)
                cost = ya if up else 1 - yb
                if not (0.35 <= cost <= 0.85):
                    continue
                won = res == ("yes" if up else "no")
                pnl = ((1.0 if won else 0.0) - cost) * HS_SHARES - _fee(cost, HS_SHARES)
                # Matt 12 Nov 15:10: "jumping on at 55 and getting off at 65 is as much of a win as a 96 closing at 100"
                # SCALP exit: sell at the bid once it is +10c over what we paid; cut it at -10c; otherwise hold to the end.
                # (1-minute candles, so real fills could be a little worse.)
                s_pnl, s_won = pnl, won
                for t, a2, b2 in c:
                    if t >= tl:
                        continue
                    bid = b2 if up else 1 - a2
                    if bid >= cost + 0.10 or bid <= cost - 0.10:
                        s_pnl = (bid - cost) * HS_SHARES - _fee(cost, HS_SHARES) - _fee(bid, HS_SHARES)
                        s_won = s_pnl > 0
                        break
                rules = {
                    "A own+4of6": agree >= 4,
                    "B own+5of6+BTC": agree >= 5 and btc_ok,
                    "C own+6of6+BTC": agree >= 6 and btc_ok,
                    "D laggard (crowd 5of6 already there, this coin behind)": crowd_lvl >= 5 and agree >= 4 and cost <= 0.65,
                }
                for rname, ok in rules.items():
                    if not ok:
                        continue
                    for ex, e_pnl, e_won in (("hold", pnl, won), ("scalp+10/-10", s_pnl, s_won)):
                        for band in ("all", "35-60c", "60-85c"):
                            if band == "35-60c" and cost > 0.60:
                                continue
                            if band == "60-85c" and cost <= 0.60:
                                continue
                            v = variants.setdefault((tl, rname, ex, band), [0, 0, 0.0, 0.0, set()])
                            v[0] += 1
                            v[1] += 1 if e_won else 0
                            v[2] += e_pnl
                            v[3] += cost
                            v[4].add(datetime.fromtimestamp(close_ts, timezone.utc).strftime("%m-%d"))
    print(f"[COMBO] ===== all coins together, last {HS_DAYS} days ({len(_rounds)} rounds), {HS_SHARES} shares, after fees =====")
    for (tl, rname, ex, band), (n, w, p, c, days) in sorted(variants.items()):
        if n == 0:
            continue
        edge = w / n - c / n
        print(f"[COMBO] {tl // 60}:00 left | {rname} | {ex} | {band}: {n} bets, won {w / n * 100:.1f}%, avg price "
              f"{c / n * 100:.1f}c, {'EDGE ' + format(edge * 100, '+.1f') + ' pts, ' if ex == 'hold' else ''}"
              f"P/L ${p:+.2f} (${p / n:+.3f} a bet)")
    print("[COMBO] done -- anything positive on several hundred bets across most days is worth live paper testing.")


def _pockets():
    """12 Nov 16:02 CT (Matt): at ~3:00 left, pick YES/NO from what the OTHER coins are doing, cross-check against every
    past close, and find the conditions where that bet never misses.
    Cells = (decision time, how many of the other 6 coins point the same way, this coin's price bucket for that side,
    whether this coin's own last-2-minute move agrees). Found on the first ~10 days (TRAIN), then checked on the last
    ~4 days it never saw (TEST) -- a pocket that only looks perfect by luck falls apart on TEST."""
    if not _rounds:
        return
    ts = sorted(_rounds)
    cut = ts[0] + (ts[-1] - ts[0]) * 0.7
    cells = {}
    for tl in (180, 120):
        for close_ts, coins in _rounds.items():
            now, moves = {}, {}
            for coin, (c, res) in coins.items():
                a, b = _at(c, tl), _at(c, tl + 120)
                if a:
                    now[coin] = (a[0] + a[1]) / 2
                if a and b:
                    moves[coin] = now[coin] - (b[0] + b[1]) / 2
            if len(now) < 5:
                continue
            for coin, mid in now.items():
                others = [v for k, v in now.items() if k != coin]
                ups = sum(1 for v in others if v > 0.5)
                downs = len(others) - ups
                up = ups > downs
                k = max(ups, downs)
                if k < 4:
                    continue
                c, res = coins[coin]
                ya, yb = _at(c, tl)
                cost = ya if up else 1 - yb
                if not (0.30 <= cost <= 0.97):
                    continue
                mv = moves.get(coin)
                own = "own-agrees" if mv is not None and (mv > 0.01 if up else mv < -0.01) else \
                      ("own-against" if mv is not None and (mv < -0.01 if up else mv > 0.01) else "own-flat")
                bucket = int(cost * 100) // 5 * 5
                won = res == ("yes" if up else "no")
                pnl = ((1.0 if won else 0.0) - cost) * HS_SHARES - _fee(cost, HS_SHARES)
                part = 0 if close_ts < cut else 1
                v = cells.setdefault((tl, k, bucket, own), [[0, 0, 0.0], [0, 0, 0.0]])[part]
                v[0] += 1
                v[1] += 0 if won else 1
                v[2] += pnl
    print(f"[POCKET] ===== pick the side the other coins point to; TRAIN = first 70% of {HS_DAYS} days, TEST = last 30% =====")
    rows = [(k, v) for k, v in cells.items() if v[0][0] >= 25]
    rows.sort(key=lambda kv: -kv[1][0][2] / kv[1][0][0])
    for (tl, k, bucket, own), (tr, te) in rows[:25]:
        print(f"[POCKET] {tl // 60}:{tl % 60:02d} left | {k}/6 others agree | {bucket}-{bucket + 4}c | {own} || TRAIN {tr[0]} bets, "
              f"lost {tr[1]}, ${tr[2]:+.2f} || TEST {te[0]} bets, lost {te[1]}, ${te[2]:+.2f}")
    never = [(k, v) for k, v in cells.items() if v[0][0] >= 20 and v[0][1] == 0]
    print(f"[POCKET] cells that NEVER lost in TRAIN (20+ bets): {len(never)}")
    for (tl, k, bucket, own), (tr, te) in sorted(never, key=lambda kv: -kv[1][0][2]):
        print(f"[POCKET] NEVER-LOST {tl // 60}:{tl % 60:02d} left | {k}/6 | {bucket}-{bucket + 4}c | {own} || TRAIN {tr[0]} "
              f"bets ${tr[2]:+.2f} || TEST {te[0]} bets, lost {te[1]}, ${te[2]:+.2f}")
    print("[POCKET] done.")


def _turn():
    """13 Nov 03:26 CT (Matt): "if all sides are down and 4 then 6 lift up, expect the other ones too -- if they're at
    45-60c buy and ride to 100". For each round and each check time, look 3 minutes back: the OTHER coins that were
    under 50c then and are over 50c now (they just turned). If K+ of them turned the same way and this coin hasn't yet
    (its price for that side is 40-60c), buy it and hold to the end."""
    res = {}
    for tl in (600, 480, 360, 240, 180):
        for close_ts, coins in _rounds.items():
            now, before = {}, {}
            for coin, (c, r) in coins.items():
                a, b = _at(c, tl), _at(c, tl + 180)
                if a and b:
                    now[coin], before[coin] = (a[0] + a[1]) / 2, (b[0] + b[1]) / 2
            if len(now) < 5:
                continue
            for coin in now:
                for up in (True, False):
                    turned = sum(1 for k in now if k != coin and
                                 ((before[k] < 0.5 < now[k]) if up else (before[k] > 0.5 > now[k])))
                    c, r = coins[coin]
                    ya, yb = _at(c, tl)
                    cost = ya if up else 1 - yb
                    if not (0.40 <= cost <= 0.60):
                        continue
                    won = r == ("yes" if up else "no")
                    pnl = ((1.0 if won else 0.0) - cost) * HS_SHARES - _fee(cost, HS_SHARES)
                    # Matt 03:26: "buy at 40 and sell at 80" -- also try selling the moment our side is bid 80c+
                    s80, w80 = pnl, won
                    for t2, a2, b2 in c:
                        if t2 >= tl:
                            continue
                        bid = b2 if up else 1 - a2
                        if bid >= 0.80:
                            s80 = (bid - cost) * HS_SHARES - _fee(cost, HS_SHARES) - _fee(bid, HS_SHARES)
                            w80 = True
                            break
                    for k in (3, 4, 5, 6):
                        if turned >= k:
                            for ex, e_p, e_w in (("hold to end", pnl, won), ("sell at 80c", s80, w80)):
                                v = res.setdefault((tl, k, ex), [0, 0, 0.0, 0.0])
                                v[0] += 1
                                v[1] += 1 if e_w else 0
                                v[2] += e_p
                                v[3] += cost
    print(f"[TURN] ===== crowd just turned (3+ / 4+ / 5+ / 6 of the other coins crossed 50c in the last 3 min), buy the "
          f"coin that hasn't (40-60c), hold to the end, {HS_DAYS} days, {HS_SHARES} shares, after fees =====")
    for (tl, k, ex), (n, w, p, c) in sorted(res.items()):
        print(f"[TURN] {tl // 60}:{tl % 60:02d} left | {k}+ others turned | {ex}: {n} bets, won {w / n * 100:.1f}%, avg price "
              f"{c / n * 100:.1f}c, P/L ${p:+.2f} (${p / n:+.3f} a bet)")
    print("[TURN] done.")


def _cheap():
    """13 Nov 03:28 CT (Matt): "it's not about winning at the end -- buy in at 10c and if it climbs to 20c that doubles the
    money". Buy the CHEAP side (5-25c) at each check time and sell the moment the bid reaches 1.5x or 2x what we paid;
    otherwise hold to the end (it usually expires at 0). Real spreads/fees included (fills at the ask, sells at the bid)."""
    res = {}
    for tl in (720, 600, 480, 360, 240):
        for close_ts, coins in _rounds.items():
            # 13 Nov 03:29 CT (Matt): "can't just bet on the low number -- there has to be a reason to expect it to go up"
            mv = {}
            for k2, (c2, r2) in coins.items():
                x, y = _at(c2, tl), _at(c2, tl + 120)
                if x and y:
                    mv[k2] = (x[0] + x[1]) / 2 - (y[0] + y[1]) / 2   # YES price change over the last 2 minutes
            for coin, (c, r) in coins.items():
                a = _at(c, tl)
                if not a or coin not in mv:
                    continue
                ya, yb = a
                for side in ("yes", "no"):
                    cost = ya if side == "yes" else 1 - yb
                    if not (0.05 <= cost <= 0.25):
                        continue
                    sgn = 1 if side == "yes" else -1
                    own_up = mv[coin] * sgn > 0.02                       # our cheap side already climbing
                    crowd = sum(1 for k2, m in mv.items() if k2 != coin and m * sgn > 0.02)   # other coins moving our way
                    reasons = ["no reason (any cheap side)"]
                    if own_up:
                        reasons.append("its own price rising")
                    if crowd >= 4:
                        reasons.append("4+ other coins moving its way")
                    if own_up and crowd >= 4:
                        reasons.append("BOTH")
                    band = "5-12c" if cost <= 0.12 else "13-25c"
                    won = r == side
                    hold = ((1.0 if won else 0.0) - cost) * HS_SHARES - _fee(cost, HS_SHARES)
                    for mult in (1.5, 2.0):
                        p, hit = hold, False
                        for t2, a2, b2 in c:
                            if t2 >= tl:
                                continue
                            bid = b2 if side == "yes" else 1 - a2
                            if bid >= cost * mult - 1e-9:
                                p = (bid - cost) * HS_SHARES - _fee(cost, HS_SHARES) - _fee(bid, HS_SHARES)
                                hit = True
                                break
                        for why in reasons:
                            v = res.setdefault((tl, why, band, f"sell at {mult:g}x"), [0, 0, 0.0, 0.0])
                            v[0] += 1
                            v[1] += 1 if (hit or won) else 0
                            v[2] += p
                            v[3] += cost
                    for why in reasons:
                        v = res.setdefault((tl, why, band, "hold to end"), [0, 0, 0.0, 0.0])
                        v[0] += 1
                        v[1] += 1 if won else 0
                        v[2] += hold
                        v[3] += cost
    print(f"[CHEAP] ===== buy the cheap side (5-25c), sell at 1.5x / 2x what we paid, else hold; {HS_DAYS} days, "
          f"{HS_SHARES} shares, after fees =====")
    for (tl, why, band, ex), (n, w, p, c) in sorted(res.items()):
        print(f"[CHEAP] {tl // 60}:00 left | {why} | {band} | {ex}: {n} bets, {w / n * 100:.1f}% hit target or won, avg price "
              f"{c / n * 100:.1f}c, P/L ${p:+.2f} (${p / n:+.3f} a bet, {p / max(c * HS_SHARES, 1e-9) * 100:+.1f}% of money put in)")
    print("[CHEAP] done.")


# ---------------- live paper ----------------
_started = [False]
_open = {}     # (rule, ticker) -> dict
_agg = {}      # (rule, hour) -> [n, won, pnl]
_last_table_hour = [None]
_mid_prev = {}  # ticker -> (side, mid)


def _last3(b, now):
    rounds = {}
    for ticker, n in b.items():
        if "15M" not in ticker.split("-")[0].upper() or n.get("coin") not in HS_COINS or n.get("tl") is None:
            continue
        if n.get("side") not in ("yes", "no") or n.get("yes_ask") is None or n.get("no_ask") is None:
            continue
        rounds.setdefault(ticker.split("-")[1], []).append((ticker, n))
    for rid, items in rounds.items():
        for ticker, n in items:
            tl = float(n["tl"])
            if not (120 <= tl <= 180):
                continue
            others = [m for t, m in items if t != ticker]
            for want in ("yes", "no"):
                agree = sum(1 for m in others if m.get("side") == want)
                cost = float(n["yes_ask"]) if want == "yes" else float(n["no_ask"])
                p_side = (n.get("p") or 0) if n.get("side") == want else 1 - (n.get("p") or 0)
                if not (0.50 <= cost <= 0.92):
                    continue
                rules = {
                    "L3-CROWD5": agree >= 5,                              # 5+ of the other 6 point this way
                    "L3-VALUE": p_side - cost >= 0.05,                    # board's win % at least 5 pts above the price
                    "L3-BOTH": agree >= 5 and p_side - cost >= 0.05,
                }
                for name, ok in rules.items():
                    if ok and (name, ticker) not in _open:
                        _open[(name, ticker)] = {"side": want, "cost": cost, "close_at": now + tl, "coin": n.get("coin")}


def run(client) -> None:
    if not HS_ENABLED:
        return
    if not _started[0]:
        _started[0] = True
        threading.Thread(target=_history, args=(client,), name="hour-study", daemon=True).start()
        print("[HOUR PAPER] started -- paper only; history study running in the background.")
    now = time.time()
    try:
        b = flip_star.board() or {}
    except Exception:
        b = {}
    for ticker, n in b.items():
        if "15M" not in ticker.split("-")[0].upper() or n.get("coin") not in HS_COINS or n.get("tl") is None:
            continue
        side = n.get("side")
        ya, na = n.get("yes_ask"), n.get("no_ask")
        if side not in ("yes", "no") or ya is None or na is None:
            continue
        cost = float(ya) if side == "yes" else float(na)
        prev = _mid_prev.get(ticker)
        _mid_prev[ticker] = (side, cost)
        tl = float(n["tl"])
        for name, tl0, tl1, lo, hi, rising in RULES:
            if (name, ticker) in _open or not (tl0 <= tl <= tl1 and lo <= cost <= hi):
                continue
            if rising and not (prev and prev[0] == side and cost > prev[1] + 1e-9):
                continue
            if name == "FLIP98" and not ((n.get("p") or 0) >= 0.98 - 1e-9 and n.get("flips") is not None
                                         and n["flips"] <= flip_star.FLIP_MAX_ROUND_FLIPS):
                continue
            _open[(name, ticker)] = {"side": side, "cost": cost, "close_at": now + tl, "coin": n.get("coin")}
    # 12 Nov 16:01 CT (Matt): last ~3 minutes, use the edge board + what the other coins are doing
    _last3(b, now)
    # settle
    for (name, ticker), p in list(_open.items()):
        if now < p["close_at"] + 60:
            continue
        try:
            res = client.get_market(ticker).get("market", {}).get("result")
        except Exception:
            continue
        if res not in ("yes", "no"):
            if now > p["close_at"] + 1800:
                _open.pop((name, ticker), None)
            continue
        for (nm, tk), q in list(_open.items()):   # settle every rule on this ticker with one lookup
            if tk != ticker or now < q["close_at"] + 60:
                continue
            _open.pop((nm, tk), None)
            won = res == q["side"]
            pnl = ((1.0 if won else 0.0) - q["cost"]) * HS_SHARES - _fee(q["cost"], HS_SHARES)
            h = _ct_hour(q["close_at"])
            a = _agg.setdefault((nm, h), [0, 0, 0.0])
            a[0] += 1
            a[1] += 1 if won else 0
            a[2] += pnl
            print(f"[HOUR PAPER] {nm} {tk} {q['side'].upper()} @ {q['cost']*100:.0f}c -> {'WON' if won else 'LOST'} "
                  f"{pnl:+.2f} (hour {h:02d} CT now {a[2]:+.2f} over {a[0]})")
    h_now = _ct_hour(now)
    if _last_table_hour[0] != h_now:
        if _last_table_hour[0] is not None and _agg:
            _table("LIVE PAPER since restart", _agg)
        _last_table_hour[0] = h_now
