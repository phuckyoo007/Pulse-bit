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
import uuid
from datetime import datetime, timezone

import flip_star
import markets_paper as mk

BOUNCE_ENABLED = True
BOUNCE_PAPER = True   # 15 Nov 14:34 CT (Matt): back to PAPER "until you refine them, perfect them" (was live 1 share 14:06-14:34)
BOUNCE_SHARES = 1
BOUNCE_LOSS_LIMIT = 10.00   # per version; a version that loses $10 net switches itself off
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
# 15 Nov 16:45 CT (Matt: "improve the paper versions so they don't have any losses").  Paper so far: 41 rides, 13 up.
#  1) Most losing BOUNCE rides came in rounds where 3-4 coins all triggered the SAME way within a minute
#     (16:30 round: ZEC/XRP/SOL/BTC YES all cut; 17:00 round: DOGE/BNB/SOL/XRP NO all cut) -- that is the whole
#     market trending, not a bounce.  Skip when 2+ other coins' cheap side on the same YES/NO side fell in the last minute.
#  2) Only buy a cheap side the board's model thinks is UNDER-priced: model chance >= ask + BOUNCE_MODEL_EDGE.
#     Markets with no model (commodities / currencies) are skipped -- the one currency ride (EUR/USD) lost.
BOUNCE_MARKET_MOVE = 2
BOUNCE_MODEL_EDGE = 0.03

# 15 Nov 13:16 CT (Matt): "it climbs really fast on the low end, 5-10c doubles your money ... buy at 3c, exit at 12c,
# a dollar turns into four ... try it with dimes ($0.10)".  Two paper versions side by side:
#   BOUNCE = cheap side 10-30c, sell at +50%     (his first idea, $1 a ride)
#   LOW    = cheap side 3-15c, sell at double     ($0.10 a ride, like he asked)
VARIANTS = [
    {"tag": "BOUNCE", "dollars": BOUNCE_DOLLARS, "lo": BOUNCE_MIN_PRICE, "hi": BOUNCE_MAX_PRICE,
     "swing": BOUNCE_SWING, "take": BOUNCE_TAKE, "stop": BOUNCE_STOP},
    {"tag": "LOW", "dollars": 0.10, "lo": 0.03, "hi": 0.15, "swing": 0.04, "take": 2.00, "stop": 0.50},
    # 15 Nov 16:45 CT paper refinement: BOUNCE's -40% stop fired on 16 of 23 rides, mostly 20-40s after buying (normal
    # wiggle), while winners ran +50-148%.  Same entry, NO stop -- hold for +50% or sell with a minute left.
    {"tag": "BOUNCE-NS", "dollars": BOUNCE_DOLLARS, "lo": BOUNCE_MIN_PRICE, "hi": BOUNCE_MAX_PRICE,
     "swing": BOUNCE_SWING, "take": BOUNCE_TAKE, "stop": 0.0},
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


def owned() -> set:
    return {k[1] for k, p in _pos.items() if p.get("live")}


def _tag():
    return "[BOUNCE-PAPER]" if BOUNCE_PAPER else "[BOUNCE]"


def _buy_live(client, ticker, side, cost):
    import flip_star as fs
    yes_price = cost if side == "yes" else round(1 - cost, 2)
    try:
        client.create_order(ticker=ticker, client_order_id=f"pulse-bounce-{uuid.uuid4()}",
                            side="bid" if side == "yes" else "ask", count=str(BOUNCE_SHARES),
                            price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
    except Exception as e:
        print(f"[BOUNCE] {ticker}: buy failed ({e}).")
        return 0, None
    for _ in range(4):
        try:
            held, exposure = fs._held_count(client, ticker)
        except Exception:
            held, exposure = 0.0, 0.0
        if abs(held) > 1e-9:
            return abs(held), (exposure / abs(held) if exposure > 0 else cost)
        time.sleep(0.4)
    return 0, None


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


def _close(key, p, bid, why, client=None, place_exit=None, settled=False):
    ticker = key[1]
    T = _tot[p["v"]]
    n = p["count"]
    if p.get("live") and not settled:
        try:
            r = place_exit(client, ticker, "ask" if p["side"] == "yes" else "bid", n, dry_run=False, entry_price=p["cost"])
        except Exception as e:
            print(f"[BOUNCE] {ticker}: sell failed ({e}) -- retrying next pass.")
            return False
        if isinstance(r, dict) and r.get("skipped"):
            print(f"[BOUNCE] {ticker}: sell skipped ({r}) -- will count it at settlement.")
            p["await_settle"] = True
            return False
        if not (isinstance(r, dict) and (r.get("_sold") or 0) > 1e-9):
            return False
        bid = r.get("_real_exit_px", bid)
    pnl = (bid - p["cost"]) * n - _fee(p["cost"], n) - (_fee(bid, n) if bid > 0 else 0)
    k = T["by"].setdefault(p["kind"], [0, 0, 0.0])
    T["n"] += 1
    T["up"] += 1 if pnl > 0 else 0
    T["pnl"] += pnl
    k[0] += 1
    k[1] += 1 if pnl > 0 else 0
    k[2] += pnl
    by = "  ".join(f"{a}: {b[0]} rides {b[2]:+.2f}" for a, b in sorted(T["by"].items()))
    _pos.pop(key, None)
    if T["pnl"] <= -BOUNCE_LOSS_LIMIT and not T.get("off"):
        T["off"] = True
        print(f"{_tag()} {p['v']} OFF -- it has lost ${-T['pnl']:.2f} (limit ${BOUNCE_LOSS_LIMIT:.0f}).")
    print(f"{_tag()} {p['v']} SELL {ticker} ({p['kind']}) {p['side'].upper()} {n:g} @ {bid*100:.0f}c (bought "
          f"{p['cost']*100:.0f}c, {time.time() - p['at']:.0f}s): {why} -> {pnl:+.2f} | {p['v']} total {T['n']} rides, "
          f"{T['up']} up, {T['pnl']:+.2f} | {by}")
    return True


def _market_falling(ticker, side, now):
    """How many OTHER crypto markets' `side` price fell 3c+ over the last minute."""
    n = 0
    for t2, h2 in _hist.items():
        if t2 == ticker or _kind(t2) != "crypto" or len(h2) < 2:
            continue
        old = [x for x in h2 if now - x[0] >= 50]
        if not old:
            continue
        o, c = old[-1], h2[-1]
        m_old = (o[1] + o[2]) / 2 if side == "yes" else 1 - (o[1] + o[2]) / 2
        m_now = (c[1] + c[2]) / 2 if side == "yes" else 1 - (c[1] + c[2]) / 2
        if m_old - m_now >= 0.03:
            n += 1
    return n


def T_off(v):
    return _tot[v["tag"]].get("off", False)


def _settle_left(client, place_exit):
    """Rides whose sell could not go through (no bid) -- count them at settlement."""
    for key, p in list(_pos.items()):
        if not p.get("await_settle") and time.time() - p["at"] < 1200:
            continue
        try:
            m = client.get_market(key[1]).get("market", {})
        except Exception:
            continue
        res = m.get("result")
        if res in ("yes", "no"):
            _close(key, p, 1.0 if res == p["side"] else 0.0, f"settled {res.upper()}", settled=True)


def run(client, place_exit=None, state=None) -> None:
    if not BOUNCE_ENABLED or place_exit is None:
        return
    now = time.time()
    if now - _last[0] < BOUNCE_POLL:
        return
    _last[0] = now
    if any(p.get("live") for p in _pos.values()):
        _settle_left(client, place_exit)
    utc = datetime.now(timezone.utc)
    rkey = utc.strftime("%Y%m%d%H") + str(utc.minute // 15)
    if _round["key"] != rkey and (utc.minute % 15) >= 1:
        _round["key"] = rkey
        _round["mk"] = []   # 15 Nov 16:45 CT: crypto only now (needs the board's model) -- no commodity/currency lookups,
        # which also saves Kalshi API calls (the bot has been getting "too many requests" on its balance check)
        _done.clear()
    quotes = {}   # ticker -> (yes_bid, yes_ask, secs_left)
    model = {}    # ticker -> model chance that YES wins (crypto board)
    try:
        for t, n in (flip_star.board() or {}).items():
            if n.get("p") is not None and n.get("side") in ("yes", "no"):
                model[t] = float(n["p"]) if n["side"] == "yes" else 1 - float(n["p"])
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
                if p.get("await_settle"):
                    continue
                bid = yb if p["side"] == "yes" else 1 - ya
                if bid >= p["cost"] * v["take"] - 1e-9:
                    _close(key, p, bid, f"+{(bid / p['cost'] - 1)*100:.0f}% -- cashing out", client, place_exit)
                elif v["stop"] > 0 and bid <= p["cost"] * v["stop"] + 1e-9:
                    _close(key, p, bid, f"cut at {bid / p['cost']*100:.0f}% of cost", client, place_exit)
                elif sl < BOUNCE_EXIT_SECONDS_LEFT:
                    _close(key, p, bid, f"{sl:.0f}s left -- out", client, place_exit)
                continue
            if key in _done or sl < v.get("min_left", BOUNCE_MIN_SECONDS_LEFT) or len(h) < 10 or T_off(v):
                continue
            if (not BOUNCE_PAPER and any(k[1] == t for k in _pos)) or (state is not None and t in state):
                continue   # one bounce ride per market, and never a market another strategy holds
            for side in ("yes", "no"):
                ask = ya if side == "yes" else 1 - yb
                if not (v["lo"] - 1e-9 <= ask <= v["hi"] + 1e-9) or ya - yb > 0.04:
                    continue
                if t not in model:
                    continue   # no model chance for this market
                chance = model[t] if side == "yes" else 1 - model[t]
                if chance < ask + BOUNCE_MODEL_EDGE - 1e-9:
                    continue   # the board doesn't think this cheap side is under-priced
                if _market_falling(t, side, now) >= BOUNCE_MARKET_MOVE:
                    continue   # the whole market is moving against this side -- a trend, not a bounce
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
                    _done.add(key)
                    if not BOUNCE_PAPER:
                        try:
                            held, _ = flip_star._held_count(client, t)
                        except Exception:
                            held = 1
                        if held != 0:
                            break   # something (the bot or Matt) already holds this market
                        count, fill = _buy_live(client, t, side, ask)
                        if not count:
                            print(f"[BOUNCE] {v['tag']} {t}: {side.upper()} @ {ask*100:.0f}c did not fill.")
                            break
                        ask = fill
                    _pos[key] = {"v": v["tag"], "side": side, "cost": ask, "count": count, "at": now, "kind": _kind(t),
                                 "live": not BOUNCE_PAPER}
                    print(f"{_tag()} {v['tag']} BUY {t} ({_kind(t)}) {side.upper()} {count} @ {ask*100:.0f}c "
                          f"(~${count*ask:.2f}): cheap side swung {lo_before_hi*100:.0f}c -> {hi*100:.0f}c and is back at "
                          f"{now_mid*100:.0f}c, {sl:.0f}s left")
                    break
    for t in [t for t in _hist if t not in quotes and t not in {k[1] for k in _pos}]:
        _hist.pop(t, None)
