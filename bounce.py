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
BOUNCE_LOSS_LIMIT = (25.00 if BOUNCE_PAPER else 10.00)   # paper versions get $25 room to collect data; per version; a version that loses $10 net switches itself off
BOUNCE_DOLLARS = 1.00
BOUNCE_POLL = 2   # 16 Nov: 2s so LAST120 can check a 4-second hold (crypto quotes come from the board cache -- no extra API calls)
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
# 15 Nov 22:10 CT: the 14-day history test ([LATE5 TEST], 4,988 bets) killed the last-5-minutes cheap-side idea:
# -25% of the money put in with every exit (double, +50%, any profit).  When the cheap side lost at the end (93% of
# the time) only 14-23% of rides ever showed a profit.  All LATE5* versions are switched OFF below.
_LATE5_OFF = True
VARIANTS = [
    {"tag": "BOUNCE", "off": True, "dollars": BOUNCE_DOLLARS, "lo": BOUNCE_MIN_PRICE, "hi": BOUNCE_MAX_PRICE,
     "swing": BOUNCE_SWING, "take": BOUNCE_TAKE, "stop": BOUNCE_STOP},
    {"tag": "LOW", "off": True, "dollars": 0.10, "lo": 0.03, "hi": 0.15, "swing": 0.04, "take": 2.00, "stop": 0.50},
    # 15 Nov 16:45 CT paper refinement: BOUNCE's -40% stop fired on 16 of 23 rides, mostly 20-40s after buying (normal
    # wiggle), while winners ran +50-148%.  Same entry, NO stop -- hold for +50% or sell with a minute left.
    {"tag": "BOUNCE-NS", "off": True, "dollars": BOUNCE_DOLLARS, "lo": BOUNCE_MIN_PRICE, "hi": BOUNCE_MAX_PRICE,
     "swing": BOUNCE_SWING, "take": BOUNCE_TAKE, "stop": 0.0},
    # 15 Nov 13:18 CT (Matt): "in the first five minutes it's all the way up to 90 and the other side is 10 -- the 90
    # comes back down to 70/60 and the 10 goes to 40".  No bounce needed: first 5 minutes, cheap side 5-12c, sell at 2x.
    {"tag": "EARLY10", "dollars": 0.10, "lo": 0.05, "hi": 0.12, "swing": 0.0, "take": 2.00, "stop": 0.0,
     "min_left": 600},
    # 15 Nov 19:36 CT (Matt): "nothing to do with how they finish -- buy in the last five minutes and walk with the
    # profit".  So these never hold to the end: sell at the target, or sell whatever it's worth with 20s left.
    # LATE5-50 takes +50% instead of waiting for a double.
    # 15 Nov 19:35 CT (Matt): "in the last five minutes the favourite is at 90-95% and falls from 95 to 85 -- the other
    # side goes 5% -> 10-15%".  Buy the 3-10c side in the last 5 minutes (down to 45s left), sell at DOUBLE, otherwise
    # hold it to the end (a flip pays $1).  No stop.  LATE5 = pure price idea; LATE5-M = only when the board's model
    # also says that side is under-priced.
    {"tag": "LATE5", "off": True, "dollars": 1.00, "lo": 0.03, "hi": 0.10, "swing": 0.0, "take": 2.00, "stop": 0.0,
     "min_left": 45, "max_left": 300, "exit_left": 20, "model": False},
    {"tag": "LATE5-M", "off": True, "dollars": 1.00, "lo": 0.03, "hi": 0.10, "swing": 0.0, "take": 2.00, "stop": 0.0,
     "min_left": 45, "max_left": 300, "exit_left": 20, "model": True},
    # 15 Nov 19:37 CT (Matt): "it doesn't have to wait for a double -- capture any profit".  LATE5-ANY sells the
    # moment selling would make money after Kalshi fees both ways.
    {"tag": "LATE5-ANY", "off": True, "dollars": 1.00, "lo": 0.03, "hi": 0.10, "swing": 0.0, "take": 0.0, "stop": 0.0,
     "min_left": 45, "max_left": 300, "exit_left": 20, "model": False, "any_profit": True},
    # 15 Nov 21:39 CT (Matt: "keep developing strategies on paper, look on the net for which ones work, test until one
    # is positive").  Live paper LATE5 lost ~$5 in one round when 5 coins all failed together -> LATE5-1 = one coin per round.
    {"tag": "LATE5-1", "off": True, "dollars": 1.00, "lo": 0.03, "hi": 0.10, "swing": 0.0, "take": 2.00, "stop": 0.0,
     "min_left": 45, "max_left": 300, "exit_left": 20, "model": False, "per_round": 1},
    # From the web (dev.to "momentum lag arbitrage", clawd "15min signal optimization"): after a sharp spot move the
    # 15-minute market price lags the spot-implied chance by 30-90s.  Our board's model IS the spot-implied chance.
    # LAG8: minutes 4-10 of the round, buy the side whose model chance is 8+ pts above its price (15-85c), sell when the
    # price catches up to the model (or the model drops below what we paid), else sell with 60s left.
    # LAG12: same with a 12-pt gap.  LAG8-H: same entry, hold to the end.
    {"tag": "LAG8", "off": True, "kind": "lag", "dollars": 1.00, "lo": 0.15, "hi": 0.85, "edge": 0.08, "swing": 0.0, "take": 0.0,
     "stop": 0.0, "min_left": 300, "max_left": 660, "exit_left": 60, "converge": True},
    {"tag": "LAG12", "kind": "lag", "dollars": 1.00, "lo": 0.15, "hi": 0.85, "edge": 0.12, "swing": 0.0, "take": 0.0,
     "stop": 0.0, "min_left": 300, "max_left": 660, "exit_left": 60, "converge": True},
    {"tag": "LAG8-H", "off": True, "kind": "lag", "dollars": 1.00, "lo": 0.15, "hi": 0.85, "edge": 0.08, "swing": 0.0, "take": 0.0,
     "stop": 0.0, "min_left": 300, "max_left": 660, "exit_left": 0},
    # 16 Nov 01:20 CT paper check: BOUNCE (32 rides, -$6.52), LAG8 (42, -$10.42), LOW (9, 0 up), BOUNCE-NS and LAG8-H
    # (hit -$10) all lose -- every cheap-side idea loses.  Research explains it: a UCD study of Kalshi (Whelan,
    # WP2025_19) found contracts under 10c lose 60%+ of the money put in (favourite-longshot bias), while contracts
    # above 50c earn small positive returns -- and "makers" (resting limit orders) earn ~1.9% after fees vs takers
    # who cross the spread.  So: MAKER90 = rest a bid 2c under the favourite's ask (88-97c ask, last 5 minutes,
    # one per coin per round); it fills only if the price comes down to us within 60s; hold to the end.
    # 16 Nov 02:40 CT: at $5 a ride MAKER90 hit the $10 paper limit after 7 rides (4 won +$0.28-0.61, 3 lost $4.35-4.72,
    # all three were 86-94c bids filled on a price DROP -- the dip kept going).  $1 a ride so it collects enough rides to judge.
    {"tag": "MAKER90", "off": True, "kind": "maker", "dollars": 1.00, "lo": 0.88, "hi": 0.97, "swing": 0.0, "take": 0.0,
     "stop": 0.0, "min_left": 30, "max_left": 300, "exit_left": 0, "under": 0.02},
    # 16 Nov 04:25 CT paper check: MAKER90 at $1 = 22 rides, 18 won, -$2.05.  Fills at 86-90c: 10 won / 6 lost
    # (losses -$0.89 each wipe out +$0.12 wins); fills at 92c+: 13 won / 1 lost.  MAKER93 = same idea, only bids of 93c+.
    # 16 Nov 07:30 CT paper check: MAKER90 48 more rides, -$4.96 (its 86-90c fills keep losing $0.87-0.89) -> OFF.
    # MAKER93: 22 rides, 22 won, +$1.09 -- keep collecting (needs 50+ rides before it's a live candidate).
    {"tag": "MAKER93", "off": True, "kind": "maker", "dollars": 1.00, "lo": 0.95, "hi": 0.97, "swing": 0.0, "take": 0.0,
     "stop": 0.0, "min_left": 30, "max_left": 300, "exit_left": 0, "under": 0.02},
    # 16 Nov 10:30 CT paper check: MAKER93 reached 52 rides, 45 won, -$4.34 -- 7 losses of ~$0.95 all in two rounds
    # (9:00 and 9:15 CT) where several coins flipped together; at 93-95c it needs ~95% winners.  The live 96c strategies
    # that DO win all require the board's sigma.  MAKER93S = MAKER93 + the board must pick that side 1.5+ sigma from flipping.
    {"tag": "MAKER93S", "kind": "maker", "dollars": 1.00, "lo": 0.95, "hi": 0.97, "swing": 0.0, "take": 0.0,
     "stop": 0.0, "min_left": 30, "max_left": 300, "exit_left": 0, "under": 0.02, "min_sigma": 1.5},
    # 16 Nov 11:19 CT (Matt): "new strategy -- last 120 seconds, anything 86c and over on every coin, the price has to
    # HOLD 86+ for four seconds before buying, 85% stop loss, early exit at 99c, floor 86 ceiling 97".
    # LAST120: favourite side 86-97c with 120s or less left, priced 86c+ for 4s straight -> buy (~$5); sell at 99c, or if it
    # falls to 85% of what we paid; otherwise held to the end.  One bet per coin per round.  PAPER until Matt says live.
    {"tag": "LAST120", "off": True, "kind": "fav", "dollars": 5.00, "lo": 0.86, "hi": 0.97, "swing": 0.0, "take": 0.0,
     "stop": 0.85, "min_left": 5, "max_left": 120, "exit_left": 0, "hold_secs": 4, "take_price": 0.99},
    # 16 Nov 11:26 CT (Matt: Ride 82 off "till you make the changes that will fix it to positive").  Ride 82 lost
    # -$6.09 over 28 bets: 9 losses of ~-$1.05 each (its 45% stop sells after a 50c+ fall) vs ~+$0.15 wins.  Late 78 (same
    # idea, +$6.57) cuts losers early when sigma drops under 1.0.  R82-FIX = Ride 82's entry (82-97c, last 4 min) + board
    # sigma 1.3+ held 4s + Late 78's exits (sell when sigma < 1.0 or the board flips; sell at 99c; 80% backup stop).
    # $2/ride (~2 shares, like Ride 82).  If it's positive over 30+ rides, Ride 82 goes back on with these rules.
    {"tag": "R82-FIX", "kind": "fav", "dollars": 2.00, "lo": 0.82, "hi": 0.97, "swing": 0.0, "take": 0.0,
     "stop": 0.80, "min_left": 15, "max_left": 240, "exit_left": 0, "hold_secs": 4, "take_price": 0.99,
     "min_sigma": 1.3, "sigma_exit": 1.0},
    {"tag": "LATE5-50", "off": True, "dollars": 1.00, "lo": 0.03, "hi": 0.10, "swing": 0.0, "take": 1.50, "stop": 0.0,
     "min_left": 45, "max_left": 300, "exit_left": 20, "model": False},
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


_per_round = {}   # (variant tag, round key) -> entries this round
_orders = {}      # (variant tag, ticker) -> paper resting bid {"side", "price", "at"}


def _enter(v, key, t, side, ask, sl, now, client, why):
    """Paper entry (lag variants)."""
    if not BOUNCE_PAPER:
        return
    _per_round[(v["tag"], _round["key"])] = _per_round.get((v["tag"], _round["key"]), 0) + 1
    count = max(1, math.floor(v["dollars"] / ask + 1e-9))
    _done.add(key)
    _pos[key] = {"v": v["tag"], "side": side, "cost": ask, "count": count, "at": now, "kind": _kind(t), "live": False}
    print(f"{_tag()} {v['tag']} BUY {t} ({_kind(t)}) {side.upper()} {count} @ {ask*100:.0f}c (~${count*ask:.2f}): {why}, "
          f"{sl:.0f}s left")


def T_off(v):
    return v.get("off", False) or _tot[v["tag"]].get("off", False)


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
    zside = {}    # ticker -> (board side, sigma from flipping)
    try:
        for t, n in (flip_star.board() or {}).items():
            if n.get("p") is not None and n.get("side") in ("yes", "no"):
                model[t] = float(n["p"]) if n["side"] == "yes" else 1 - float(n["p"])
                if n.get("zs") is not None:
                    zside[t] = (n["side"], float(n["zs"]))
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
                _net = (bid - p["cost"]) * p["count"] - _fee(p["cost"], p["count"]) - _fee(bid, p["count"])
                _ch = None
                if t in model:
                    _ch = model[t] if p["side"] == "yes" else 1 - model[t]
                _zs = zside.get(t)
                if v.get("sigma_exit") and _zs and (_zs[0] != p["side"] or _zs[1] < v["sigma_exit"] - 1e-9):
                    _close(key, p, bid, f"sigma {('flipped' if _zs[0] != p['side'] else f'{_zs[1]:.2f}')} -- out",
                           client, place_exit)
                elif v.get("take_price") and bid >= v["take_price"] - 1e-9:
                    _close(key, p, bid, f"hit {v['take_price']*100:.0f}c -- cashing in", client, place_exit)
                elif v.get("converge") and _ch is not None and bid >= _ch - 0.01 and _net > 0.005:
                    _close(key, p, bid, f"price caught up to the model ({_ch*100:.0f}%) -- taking +${_net:.2f}",
                           client, place_exit)
                elif v.get("converge") and _ch is not None and _ch < p["cost"] - 1e-9:
                    _close(key, p, bid, f"model fell to {_ch*100:.0f}% (paid {p['cost']*100:.0f}c) -- out",
                           client, place_exit)
                elif v.get("any_profit") and _net > 0.005:
                    _close(key, p, bid, f"+${_net:.2f} after fees -- taking it", client, place_exit)
                elif v["take"] > 0 and bid >= p["cost"] * v["take"] - 1e-9:
                    _close(key, p, bid, f"+{(bid / p['cost'] - 1)*100:.0f}% -- cashing out", client, place_exit)
                elif v["stop"] > 0 and bid <= p["cost"] * v["stop"] + 1e-9:
                    _close(key, p, bid, f"cut at {bid / p['cost']*100:.0f}% of cost", client, place_exit)
                elif v.get("exit_left", BOUNCE_EXIT_SECONDS_LEFT) > 0 and sl < v.get("exit_left", BOUNCE_EXIT_SECONDS_LEFT):
                    _close(key, p, bid, f"{sl:.0f}s left -- out", client, place_exit)
                elif v.get("exit_left", BOUNCE_EXIT_SECONDS_LEFT) == 0 and sl < 4:
                    # held to the end: count it as settled -- a side priced 50c+ in the last seconds wins $1
                    _close(key, p, 1.0 if bid >= 0.5 else 0.0, f"held to the end -- {'WON' if bid >= 0.5 else 'lost'}",
                           client, place_exit, settled=True)
                continue
            if key in _done or sl < v.get("min_left", BOUNCE_MIN_SECONDS_LEFT) or sl > v.get("max_left", 9999) \
                    or len(h) < 10 or T_off(v):
                continue
            if (not BOUNCE_PAPER and any(k[1] == t for k in _pos)) or (state is not None and t in state):
                continue   # one bounce ride per market, and never a market another strategy holds
            if v.get("per_round") and _per_round.get((v["tag"], _round["key"]), 0) >= v["per_round"]:
                continue
            if v.get("kind") == "fav":
                fav = "yes" if (ya + yb) / 2 >= 0.5 else "no"
                ask_f = ya if fav == "yes" else 1 - yb
                if not (v["lo"] - 1e-9 <= ask_f <= v["hi"] + 1e-9) or ya - yb > 0.04:
                    continue
                if v.get("min_sigma"):
                    zs_ = zside.get(t)
                    if not zs_ or zs_[0] != fav or zs_[1] < v["min_sigma"] - 1e-9:
                        continue
                # held 86c+ for hold_secs straight (every reading in that window, at least 2 readings)
                win = [(x[2] if fav == "yes" else 1 - x[1]) for x in h if now - x[0] <= v["hold_secs"] + 0.5]
                if len(win) < 2 or min(win) < v["lo"] - 1e-9 or now - min(x[0] for x in h if now - x[0] <= v["hold_secs"] + 0.5) < v["hold_secs"] - 0.5:
                    continue
                _enter(v, key, t, fav, ask_f, sl, now, client, f"{fav.upper()} held {v['lo']*100:.0f}c+ for {v['hold_secs']}s")
                continue
            if v.get("kind") == "maker":
                o = _orders.get(key)
                if o:
                    ask_o = ya if o["side"] == "yes" else 1 - yb
                    if ask_o <= o["price"] + 1e-9:   # the price came down to our resting bid -> filled
                        _orders.pop(key, None)
                        _done.add(key)
                        count = max(1, math.floor(v["dollars"] / o["price"] + 1e-9))
                        _pos[key] = {"v": v["tag"], "side": o["side"], "cost": o["price"], "count": count, "at": now,
                                     "kind": _kind(t), "live": False}
                        print(f"{_tag()} {v['tag']} FILLED {t} {o['side'].upper()} {count} @ {o['price']*100:.0f}c "
                              f"(resting bid hit after {now - o['at']:.0f}s), {sl:.0f}s left -- holding to the end")
                    elif now - o["at"] > 60:
                        _orders.pop(key, None)   # not filled in 60s -- cancel, may re-post next pass
                    continue
                fav = "yes" if (ya + yb) / 2 >= 0.5 else "no"
                ask_f = ya if fav == "yes" else 1 - yb
                if v.get("min_sigma"):
                    zs_ = zside.get(t)
                    if not zs_ or zs_[0] != fav or zs_[1] < v["min_sigma"] - 1e-9:
                        continue   # the board must agree AND be min_sigma+ from flipping
                if v["lo"] - 1e-9 <= ask_f <= v["hi"] + 1e-9 and ya - yb <= 0.04:
                    _orders[key] = {"side": fav, "price": round(max(0.85, ask_f - v["under"]), 2), "at": now}
                continue
            for side in ("yes", "no"):
                ask = ya if side == "yes" else 1 - yb
                if not (v["lo"] - 1e-9 <= ask <= v["hi"] + 1e-9) or ya - yb > 0.04:
                    continue
                if v.get("kind") == "lag":
                    if t not in model:
                        continue
                    chance = model[t] if side == "yes" else 1 - model[t]
                    if chance - ask < v["edge"] - 1e-9:
                        continue
                    _enter(v, key, t, side, ask, sl, now, client,
                           f"model {chance*100:.0f}% vs price {ask*100:.0f}c (+{(chance-ask)*100:.0f} pts)")
                    break
                if t not in model:
                    continue   # no model chance for this market
                chance = model[t] if side == "yes" else 1 - model[t]
                if v.get("model", True) and chance < ask + BOUNCE_MODEL_EDGE - 1e-9:
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
                    _per_round[(v["tag"], _round["key"])] = _per_round.get((v["tag"], _round["key"]), 0) + 1
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
