"""
CLOSER paper strategies (5 Nov 23:38 CT, Matt) -- PAPER ONLY, nothing is bought.

The first Closer rule (favourite at 70-95c with 3:00-1:30 left) lost on paper (38 bets, -$11.47) and is retired.
Two rules from the recorder data (109 coin-rounds, 5 Nov) are now paper-tested side by side:

  FLIPS  (3:00 -> 2:00 left): buy the board's pick at 70-95c if it has flipped 2 times or fewer this round and the
          momentum meter is NOT pushing hard its way (below +30 for the pick). Data: 18 bets, 94% won, +10c/share.
  RISING (2:00 -> 1:00 left): buy whichever side is above 50c (below 97c) and has risen over the last ~20-45 seconds.
          Data: 27 bets, 93% won, +10c/share.  (Same rule at 2:30-3:00 left LOST -- it only works late.)

Each rule: one paper bet per coin per round, held to the end, scored with Kalshi's real result and fee.
Log lines: "[CLOSER-PAPER] <RULE> BUY ..." and "[CLOSER-PAPER] <RULE> RESULT ... -- <RULE> paper total ...".
"""
import math
import time

import flip_star
import surge

CLOSER_ENABLED = True
RULES = {
    "FLIPS":  {"shares": 5, "from": 180, "until": 120},
    "RISING": {"shares": 2, "from": 120, "until": 60},
}
FLIPS_MIN_PRICE, FLIPS_MAX_PRICE, FLIPS_MAX_FLIPS, FLIPS_MAX_MOM_WITH = 0.70, 0.95, 2, 0.30
RISING_MIN_PRICE, RISING_MAX_PRICE = 0.50, 0.97
RISING_LOOKBACK = (20, 45)   # compare with the price 20-45 seconds ago

_hist = {}    # ticker -> [(t, yes_ask, no_ask)]
_paper = {}   # (rule, ticker) -> bet
_tally = {r: {"n": 0, "w": 0, "pnl": 0.0} for r in RULES}


def _fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def _buy(rule, ticker, n, side, px, tl, why, now):
    _paper[(rule, ticker)] = {"coin": n.get("coin"), "side": side, "px": float(px), "close_at": now + tl}
    print(f"[CLOSER-PAPER] {rule} BUY {ticker} ({n.get('coin')}): {side.upper()} @ {px*100:.0f}c, {int(tl)}s left, "
          f"{why} -- {RULES[rule]['shares']} shares (paper)")


def run(client) -> None:
    if not CLOSER_ENABLED:
        return
    now = time.time()
    try:
        b = flip_star.board()
    except Exception:
        b = {}
    for ticker, n in (b or {}).items():
        if "15M" not in ticker.split("-")[0].upper() or n.get("tl") is None:
            continue
        ya, na, tl = n.get("yes_ask"), n.get("no_ask"), n["tl"]
        h = _hist.setdefault(ticker, [])
        h.append((now, ya, na))
        while h and now - h[0][0] > 90:
            h.pop(0)
        # FLIPS
        r = RULES["FLIPS"]
        if ("FLIPS", ticker) not in _paper and r["until"] <= tl <= r["from"]:
            side = n.get("side")
            px = ya if side == "yes" else na
            try:
                m = surge.momentum_for(ticker, side)
            except Exception:
                m = None
            if (px is not None and FLIPS_MIN_PRICE <= px <= FLIPS_MAX_PRICE and n.get("flips") is not None
                    and n["flips"] <= FLIPS_MAX_FLIPS and m is not None and m < FLIPS_MAX_MOM_WITH):
                _buy("FLIPS", ticker, n, side, px, tl, f"{n['flips']} flips, momentum {m*100:+.0f} for it", now)
        # RISING
        r = RULES["RISING"]
        if ("RISING", ticker) not in _paper and r["until"] <= tl <= r["from"]:
            old = [x for x in h if RISING_LOOKBACK[0] <= now - x[0] <= RISING_LOOKBACK[1]]
            if old:
                _, ya0, na0 = old[-1]
                for side, px, px0 in (("yes", ya, ya0), ("no", na, na0)):
                    if (px is not None and px0 is not None and RISING_MIN_PRICE < px < RISING_MAX_PRICE
                            and px > px0 + 1e-9):
                        _buy("RISING", ticker, n, side, px, tl, f"up from {px0*100:.0f}c ~{int(now - old[-1][0])}s ago", now)
                        break
    # settle with Kalshi's real result
    for key, bet in list(_paper.items()):
        rule, ticker = key
        if bet.get("done") or now < bet["close_at"] + 60:
            continue
        try:
            res = client.get_market(ticker).get("market", {}).get("result")
        except Exception:
            continue
        if res not in ("yes", "no"):
            if now > bet["close_at"] + 1800:
                bet["done"] = True
            continue
        won = res == bet["side"]
        pnl = ((1 - bet["px"] if won else -bet["px"]) - _fee(bet["px"])) * RULES[rule]["shares"]
        t = _tally[rule]
        t["n"] += 1
        t["w"] += won
        t["pnl"] += pnl
        bet["done"] = True
        print(f"[CLOSER-PAPER] {rule} RESULT {ticker} ({bet['coin']}): {'WON' if won else 'LOST'} {pnl:+.2f} -- {rule} "
              f"paper total {t['n']} bets, {t['w']} won, {t['pnl']:+.2f}")
    for key in [k for k, bet in _paper.items() if bet.get("done") and now > bet["close_at"] + 3600]:
        _paper.pop(key, None)
    for t in [t for t, h in _hist.items() if h and now - h[-1][0] > 600]:
        _hist.pop(t, None)
