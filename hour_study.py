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
    for name, *_ in RULES:
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
            hour = _ct_hour(close_ts)
            day = (datetime.fromtimestamp(close_ts, timezone.utc) - timedelta(hours=6)).strftime("%m-%d")
            for name, tl0, tl1, lo, hi, rising in RULES:
                prev = None
                for tl, ya, yb in candles:
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
        for name, *_ in RULES:
            parts = [f"{k[1]} {v[2]:+.2f} ({v[1]}/{v[0]})" for k, v in sorted(days.items()) if k[0] == name]
            print(f"[HOUR STUDY] {name} by day: " + "  |  ".join(parts))
    except Exception as e:
        print(f"[HOUR STUDY] history crashed ({e}).")


# ---------------- live paper ----------------
_started = [False]
_open = {}     # (rule, ticker) -> dict
_agg = {}      # (rule, hour) -> [n, won, pnl]
_last_table_hour = [None]
_mid_prev = {}  # ticker -> (side, mid)


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
