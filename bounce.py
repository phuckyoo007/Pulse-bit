"""
BOUNCE -- PAPER (15 Nov 13:14 CT, Matt): "when it's bouncing -- the favourite goes 80 down to 70 and back up to 80 --
buy a dollar of the OTHER side at about 20c, watch that dollar turn into $1.50, cash out the 50c, every time."

Every BOUNCE_POLL seconds, for every 15-minute market on all the boards (crypto edge board + commodities, financials
and currencies from the Markets board):
  * keeps each market's last BOUNCE_LOOKBACK seconds of prices;
  * BOUNCING = the cheap side has already swung up at least BOUNCE_SWING (e.g. 20c -> 30c) and come back down in that
    window, and it is back near the bottom of that swing now;
  * BUY about $1 of the cheap side (BOUNCE_MIN_PRICE..BOUNCE_MAX_PRICE), at least BOUNCE_MIN_SECONDS_LEFT left;
  * SELL when it's worth 1.5x what we paid (+50%), or cut it at BOUNCE_STOP x, or with under a minute left.
Buys at the real ask, sells at the real bid, Kalshi fees both ways. One ride per market per round. Logged [BOUNCE-PAPER].
"""
import math
import time
from datetime import datetime, timezone

import flip_star
import markets_paper as mk

BOUNCE_ENABLED = True
BOUNCE_PAPER = True
BOUNCE_DOLLARS = 1.00
BOUNCE_POLL = 3
BOUNCE_LOOKBACK = 300
BOUNCE_SWING = 0.08
BOUNCE_MIN_PRICE = 0.10
BOUNCE_MAX_PRICE = 0.30
BOUNCE_TAKE = 1.50
BOUNCE_STOP = 0.60
BOUNCE_MIN_SECONDS_LEFT = 180
BOUNCE_EXIT_SECONDS_LEFT = 60

# 15 Nov 13:16 CT (Matt): "it climbs really fast on the low end, 5-10c doubles your money ... buy at 3c, exit at 12c,
# a dollar turns into four ... try it with dimes ($0.10)".  Two paper versions side by side:
#   BOUNCE = cheap side 10-30c, sell at +50%     (his first idea, $1 a ride)
#   LOW    = cheap side 3-15c, sell at double     ($0.10 a ride, like he asked)
VARIANTS = [
    {"tag": "BOUNCE", "dollars": BOUNCE_DOLLARS, "lo": BOUNCE_MIN_PRICE, "hi": BOUNCE_MAX_PRICE,
     "swing": BOUNCE_SWING, "take": BOUNCE_TAKE, "stop": BOUNCE_STOP},
    {"tag": "LOW", "dollars": 0.10, "lo": 0.03, "hi": 0.15, "swing": 0.04, "take": 2.00, "stop": 0.50},
    # 15 Nov 13:18 CT (Matt): "in the first five minutes it's all the way up to 90 and the other side is 10 -- the 90
    # comes back down to 70/60 and the 10 goes to 40".  No bounce needed: first 5 minutes, cheap side 5-12c, sell at 2x.
    {"tag": "EARLY10", "dollars": 0.10, "lo": 0.05, "hi": 0.12, "swing": 0.0, "take": 2.00, "stop": 0.0,
     "min_left": 600},
]

_last = [0.0]
_round = {"key": None, "mk": []}
_hist = {}     # ticker -> [(t, yes_bid, yes_ask)]
_pos = {}      # ticker -> dict
_done = set()
_tot = {v["tag"]: {"n": 0, "up": 0, "pnl": 0.0, "by": {}} for v in VARIANTS}


def _fee(p, n):
    return math.ceil(0.07 * p * (1 - p) * n * 100 - 1e-9) / 100


def _kind(t):
    if t.split("-")[0].upper() in ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M", "KXBNB15M",
                                   "KXHYPE15M", "KXZEC15M", "KXNEAR15M"):
        return "crypto"
    if mk._is_commodity(t):
        return "commodity"
    if mk._is_fx(t):
        return "currency"
    return "financial"


def _discover(client):
    out = []
    tables = dict(mk._EXTRA_PREFIXES)
    try:
        tables.update(mk._series_from_list(client))
    except Exception:
        pass
    now = time.time()
    for prefix in tables:
        if "15M" not in prefix.upper():
            continue
        try:
            for m in client.get_markets(status=None, series_ticker=prefix, limit=200, min_close_ts=now,
                                        max_close_ts=now + 16 * 60).get("markets", []):
                if m.get("status") and m.get("status") not in ("active", "open"):
                    continue
                ya = mk._f(m.get("yes_ask_dollars"))
                if ya is not None and 0.05 <= ya <= 0.95:
                    out.append(m["ticker"])
        except Exception:
            continue
    return out


def _close(key, p, bid, why):
    ticker = key[1]
    T = _tot[p["v"]]
    n = p["count"]
    pnl = (bid - p["cost"]) * n - _fee(p["cost"], n) - (_fee(bid, n) if bid > 0 else 0)
    k = T["by"].setdefault(p["kind"], [0, 0, 0.0])
    T["n"] += 1
    T["up"] += 1 if pnl > 0 else 0
    T["pnl"] += pnl
    k[0] += 1
    k[1] += 1 if pnl > 0 else 0
    k[2] += pnl
    by = "  ".join(f"{a}: {b[0]} rides {b[2]:+.2f}" for a, b in sorted(T["by"].items()))
    print(f"[BOUNCE-PAPER] {p['v']} SELL {ticker} ({p['kind']}) {p['side'].upper()} {n:g} @ {bid*100:.0f}c (bought "
          f"{p['cost']*100:.0f}c, {time.time() - p['at']:.0f}s): {why} -> {pnl:+.2f} | {p['v']} total {T['n']} rides, "
          f"{T['up']} up, {T['pnl']:+.2f} | {by}")


def run(client) -> None:
    if not BOUNCE_ENABLED:
        return
    now = time.time()
    if now - _last[0] < BOUNCE_POLL:
        return
    _last[0] = now
    utc = datetime.now(timezone.utc)
    rkey = utc.strftime("%Y%m%d%H") + str(utc.minute // 15)
    if _round["key"] != rkey and (utc.minute % 15) >= 1:
        _round["key"] = rkey
        _round["mk"] = _discover(client)
        _done.clear()
    quotes = {}   # ticker -> (yes_bid, yes_ask, secs_left)
    try:
        for t, n in (flip_star.board() or {}).items():
            if "15M" in t.split("-")[0].upper() and n.get("yes_ask") is not None and n.get("no_ask") is not None \
                    and n.get("tl") is not None:
                quotes[t] = (1 - float(n["no_ask"]), float(n["yes_ask"]), float(n["tl"]))
    except Exception:
        pass
    want = set(_round["mk"]) | {k[1] for k in _pos if k[1] not in quotes}
    if want:
        try:
            for t, m in mk._batch(client, sorted(want)).items():
                yb, ya, sl = mk._f(m.get("yes_bid_dollars")), mk._f(m.get("yes_ask_dollars")), mk._secs_left(m)
                if yb is not None and ya is not None and sl is not None:
                    quotes[t] = (yb, ya, sl)
        except Exception:
            pass
    for t, (yb, ya, sl) in quotes.items():
        h = _hist.setdefault(t, [])
        h.append((now, yb, ya))
        while h and now - h[0][0] > BOUNCE_LOOKBACK:
            h.pop(0)
        for v in VARIANTS:
            key = (v["tag"], t)
            p = _pos.get(key)
            if p:
                bid = yb if p["side"] == "yes" else 1 - ya
                if bid >= p["cost"] * v["take"] - 1e-9:
                    _close(key, _pos.pop(key), bid, f"+{(bid / p['cost'] - 1)*100:.0f}% -- cashing out")
                elif v["stop"] > 0 and bid <= p["cost"] * v["stop"] + 1e-9:
                    _close(key, _pos.pop(key), bid, f"cut at {bid / p['cost']*100:.0f}% of cost")
                elif sl < BOUNCE_EXIT_SECONDS_LEFT:
                    _close(key, _pos.pop(key), bid, f"{sl:.0f}s left -- out")
                continue
            if key in _done or sl < v.get("min_left", BOUNCE_MIN_SECONDS_LEFT) or len(h) < 10:
                continue
            for side in ("yes", "no"):
                ask = ya if side == "yes" else 1 - yb
                if not (v["lo"] - 1e-9 <= ask <= v["hi"] + 1e-9) or ya - yb > 0.04:
                    continue
                mids = [((b + a) / 2 if side == "yes" else 1 - (b + a) / 2) for _, b, a in h]
                hi = max(mids)
                lo_before_hi = min(mids[:mids.index(hi) + 1])
                now_mid = mids[-1]
                # it already swung UP inside the window, and is back near the low now
                # 15 Nov 13:24 CT: first live paper rides caught CRASHES (60c -> 82c -> 29c), not bounces.  A real bounce
                # stays in the cheap zone the whole time (20 -> 30 -> 20), and today's price is back near the start,
                # not breaking lower.
                if v["swing"] > 0 and (hi > v["hi"] + 2 * v["swing"] + 1e-9 or now_mid < lo_before_hi - 0.03):
                    continue
                if hi - lo_before_hi >= v["swing"] - 1e-9 and hi - now_mid >= v["swing"] * 0.8 - 1e-9:
                    count = max(1, math.floor(v["dollars"] / ask + 1e-9))
                    _pos[key] = {"v": v["tag"], "side": side, "cost": ask, "count": count, "at": now, "kind": _kind(t)}
                    _done.add(key)
                    print(f"[BOUNCE-PAPER] {v['tag']} BUY {t} ({_kind(t)}) {side.upper()} {count} @ {ask*100:.0f}c "
                          f"(~${count*ask:.2f}): cheap side swung {lo_before_hi*100:.0f}c -> {hi*100:.0f}c and is back at "
                          f"{now_mid*100:.0f}c, {sl:.0f}s left")
                    break
    for t in [t for t in _hist if t not in quotes and t not in {k[1] for k in _pos}]:
        _hist.pop(t, None)
