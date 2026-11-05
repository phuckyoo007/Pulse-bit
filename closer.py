"""
CLOSER strategy (5 Nov 15:50 CT) -- built from the board / trade data, PAPER ONLY until Matt says go.

What the data showed (453 real bets Jul 30-Aug 2, plus ~240 board snapshots with real results on 5 Nov):
  * Buying the board's favourite with ~6 minutes left LOSES (65% win, about -15c a share). 4 minutes: still slightly negative.
  * 2-3 minutes left is where the favourite pays: 86-94% win, about +4 to +10c a share after fees.
  * 98-99c shares lose money overall (one upset wipes out dozens of 1-2c wins); 94-97c is thin but positive.
  * When the board's own model is more than 10 points BELOW the market price, the favourite wins much less often.
  * Selling early gave back value vs. holding to the end (exit_comparisons.json).

Rule:
  * Between CLOSER_FROM (3:00 left) and CLOSER_UNTIL (1:30 left), buy the board's pick if
      - its price is CLOSER_MIN_PRICE-CLOSER_MAX_PRICE (70-95c),
      - the board's win % for it is no more than CLOSER_MAX_MODEL_GAP (10 pts) below that price,
      - and the pick hasn't flipped in the last CLOSER_STABLE_SECONDS (60s).
  * One bet per coin per round, CLOSER_SHARES shares, hold to settlement.
PAPER mode: logs "[CLOSER-PAPER]" entries and the real settled result + P/L for each, nothing is bought.
"""
import math
import time

import flip_star

CLOSER_PAPER = True          # True = log only, no real orders
CLOSER_ENABLED = True
CLOSER_SHARES = 5
CLOSER_FROM = 180            # start looking with 3:00 left
CLOSER_UNTIL = 90            # stop with 1:30 left
CLOSER_MIN_PRICE = 0.70
CLOSER_MAX_PRICE = 0.95
CLOSER_MAX_MODEL_GAP = 0.10
CLOSER_STABLE_SECONDS = 60

_side_seen = {}   # ticker -> (side, since)
_paper = {}       # ticker -> {"coin", "side", "px", "close_at"}
_tally = {"n": 0, "w": 0, "pnl": 0.0}


def _fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


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
        side = n.get("side")
        prev = _side_seen.get(ticker)
        if prev is None or prev[0] != side:
            _side_seen[ticker] = (side, now)
        if ticker in _paper:
            continue
        tl = n["tl"]
        if tl > CLOSER_FROM or tl < CLOSER_UNTIL:
            continue
        if now - _side_seen[ticker][1] < CLOSER_STABLE_SECONDS:
            continue
        px = n.get("yes_ask") if side == "yes" else n.get("no_ask")
        p = n.get("p")
        if px is None or p is None:
            continue
        if px < CLOSER_MIN_PRICE - 1e-9 or px > CLOSER_MAX_PRICE + 1e-9:
            continue
        if p < px - CLOSER_MAX_MODEL_GAP - 1e-9:
            continue
        _paper[ticker] = {"coin": n.get("coin"), "side": side, "px": float(px), "close_at": now + tl}
        print(f"[CLOSER-PAPER] BUY {ticker} ({n.get('coin')}): {side.upper()} @ {px*100:.0f}c, board {p*100:.0f}%, "
              f"{int(tl)}s left -- {CLOSER_SHARES} shares (paper)")
    # settle paper bets with Kalshi's real result
    for ticker, bet in list(_paper.items()):
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
        per = (1 - bet["px"] if won else -bet["px"]) - _fee(bet["px"])
        pnl = per * CLOSER_SHARES
        _tally["n"] += 1
        _tally["w"] += won
        _tally["pnl"] += pnl
        bet["done"] = True
        print(f"[CLOSER-PAPER] RESULT {ticker} ({bet['coin']}): {'WON' if won else 'LOST'} {pnl:+.2f} -- paper total "
              f"{_tally['n']} bets, {_tally['w']} won, {_tally['pnl']:+.2f}")
    for t in [t for t, bet in _paper.items() if bet.get("done") and now > bet["close_at"] + 3600]:
        _paper.pop(t, None)
        _side_seen.pop(t, None)
