"""
FINAL60 -- PAPER (13 Nov 19:43 CT, Matt: "look up strategies that work and incorporate them").

Research (Kalshi rules + predictionmarketspicks.com tape study, Oct 2026): KXBTC15M-style crypto markets settle on the
AVERAGE of the 60 one-second CF Benchmarks index prints in the final minute -- not the last tick. Once part of that
minute has printed, that part of the average is locked in, so the outcome is more certain than a last-tick view of the
price suggests (about 71% less uncertainty with 30s left). The study found final-minute favourites earned +1.2c to
+5.5c a contract after fees (80-89c +5.47c, 90-94c +3.70c, 95-99c +1.22c) where every earlier entry lost.

This module rebuilds that average live from the edge board's spot price every second of the final minute and works out
the real win chance:
    known part   = average of the prints so far (k seconds)
    unknown part = r = 60 - k remaining seconds, a random walk from today's spot
    final avg    ~ Normal( (k*known + r*spot)/60 ,  spot*vol_per_sec*sqrt(r^3/3)/60 )
and buys the side whose true chance beats its price + fee by FINAL60_MIN_EDGE, held to settlement.
"""
import math
import time

import flip_star

FINAL60_ENABLED = True
FINAL60_PAPER = True
FINAL60_SHARES = 2
FINAL60_MAX_SECONDS_LEFT = 45      # need at least 15 of the 60 prints already in
FINAL60_MIN_SECONDS_LEFT = 5
FINAL60_MIN_EDGE = 0.03            # true chance - price - fee
FINAL60_MIN_PRICE = 0.55
FINAL60_MAX_PRICE = 0.97

_prints = {}   # ticker -> {second: spot}
_close = {}    # ticker -> close time
_pos = {}      # ticker -> {"side", "cost", "p", "at"}
_tot = {"n": 0, "won": 0, "pnl": 0.0}
_warned = [False]


def _fee(p, n):
    return math.ceil(0.07 * p * (1 - p) * n * 100 - 1e-9) / 100


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def run(client) -> None:
    if not FINAL60_ENABLED:
        return
    now = time.time()
    try:
        b = flip_star.board() or {}
    except Exception:
        b = {}
    for ticker, n in b.items():
        if "15M" not in ticker.split("-")[0].upper() or n.get("tl") is None:
            continue
        tl = float(n["tl"])
        _close.setdefault(ticker, now + tl)
        spot, strike, d, vol = n.get("spot"), n.get("strike"), n.get("dir"), n.get("vol")
        if not (spot and strike and vol) or d not in (1, -1):
            if not _warned[0]:
                _warned[0] = True
                print(f"[FINAL60-PAPER] board is missing spot/strike/vol for {ticker} (spot={spot}, strike={strike}, "
                      f"dir={d}, vol={vol}) -- can't rebuild the settlement average.")
            continue
        if 0 < tl <= 60:
            _prints.setdefault(ticker, {})[int(now)] = float(spot)
        if ticker in _pos or not (FINAL60_MIN_SECONDS_LEFT <= tl <= FINAL60_MAX_SECONDS_LEFT):
            continue
        pr = _prints.get(ticker, {})
        k = len(pr)
        if k < 60 - FINAL60_MAX_SECONDS_LEFT - 3:
            continue   # missed too much of the minute (restart / gaps)
        r = max(0.0, 60 - k)
        known = sum(pr.values()) / k
        s = float(spot)
        sd = s * float(vol) / math.sqrt(31536000) * math.sqrt(max(r, 1e-9) ** 3 / 3) / 60
        exp = (k * known + r * s) / 60
        if sd <= 0:
            continue
        p_above = _phi((exp - float(strike)) / sd)
        p_yes = p_above if d == 1 else 1 - p_above
        ya, na = n.get("yes_ask"), n.get("no_ask")
        if ya is None or na is None:
            continue
        for side, p, ask in (("yes", p_yes, float(ya)), ("no", 1 - p_yes, float(na))):
            if not (FINAL60_MIN_PRICE <= ask <= FINAL60_MAX_PRICE):
                continue
            edge = p - ask - _fee(ask, FINAL60_SHARES) / FINAL60_SHARES
            if edge >= FINAL60_MIN_EDGE:
                _pos[ticker] = {"side": side, "cost": ask, "p": p, "at": now}
                print(f"[FINAL60-PAPER] BUY {ticker} {side.upper()} {FINAL60_SHARES} @ {ask*100:.0f}c: true chance "
                      f"{p*100:.1f}% (avg of {k} prints so far {known:.2f}, spot {s:.2f}, target {float(strike):.2f}, "
                      f"{tl:.0f}s left) -- edge {edge*100:+.1f}c a share")
                break
    # settle
    for ticker, p in list(_pos.items()):
        if now < _close.get(ticker, now) + 60:
            continue
        try:
            res = client.get_market(ticker).get("market", {}).get("result")
        except Exception:
            continue
        if res not in ("yes", "no"):
            continue
        _pos.pop(ticker, None)
        won = res == p["side"]
        pnl = ((1.0 if won else 0.0) - p["cost"]) * FINAL60_SHARES - _fee(p["cost"], FINAL60_SHARES)
        _tot["n"] += 1
        _tot["won"] += 1 if won else 0
        _tot["pnl"] += pnl
        print(f"[FINAL60-PAPER] {'WON' if won else 'LOST'} {ticker} {p['side'].upper()} @ {p['cost']*100:.0f}c "
              f"(model {p['p']*100:.1f}%) {pnl:+.2f} | total {_tot['n']} bets, {_tot['won']} won, {_tot['pnl']:+.2f}")
    for t in [t for t, c in _close.items() if now > c + 600]:
        _close.pop(t, None)
        _prints.pop(t, None)
