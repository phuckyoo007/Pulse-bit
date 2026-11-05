"""
Board recorder (5 Nov 15:44 CT, Matt: "based on the data from the board, develop your own new strategy").

Logs a compact snapshot of every 15-minute crypto market on the edge board every REC_EVERY seconds, plus each round's
real result once Kalshi settles it, so strategies can be tested against what actually happened. Read-only -- never trades.

  [REC] {"t":..., "k": ticker, "c": coin, "tl": secs left, "s": "y"/"n" board pick, "p": pick win %, "z": sigma to flip,
         "f": flips so far, "ya": yes ask, "na": no ask, "m": momentum (UP = +)}
  [REC-END] {"k": ticker, "c": coin, "r": "yes"/"no"}
"""
import json
import time

import flip_star
import surge

REC_EVERY = 10          # seconds between snapshots
_last = [0.0]
_open = {}              # ticker -> (coin, close estimate)
_done = set()


def _r(v, n=3):
    try:
        return None if v is None else round(float(v), n)
    except Exception:
        return None


def run(client) -> None:
    now = time.time()
    if now - _last[0] < REC_EVERY:
        return
    _last[0] = now
    try:
        b = flip_star.board()
    except Exception:
        b = {}
    for ticker, n in (b or {}).items():
        if "15M" not in ticker.split("-")[0].upper() or n.get("tl") is None:
            continue
        try:
            m = surge.momentum_for(ticker, "yes")
        except Exception:
            m = None
        print("[REC] " + json.dumps({"t": int(now), "k": ticker, "c": n.get("coin"), "tl": int(n["tl"]),
                                     "s": "y" if n.get("side") == "yes" else "n", "p": _r(n.get("p")),
                                     "z": _r(n.get("zs"), 2), "f": n.get("flips"), "ya": _r(n.get("yes_ask")),
                                     "na": _r(n.get("no_ask")), "m": _r(m)}, separators=(",", ":")))
        if ticker not in _done:
            _open[ticker] = (n.get("coin"), now + float(n["tl"]))
    # results: ask Kalshi once each round has been closed for a minute
    for ticker, (coin, close_at) in list(_open.items()):
        if now < close_at + 60:
            continue
        try:
            mk = client.get_market(ticker).get("market", {})
        except Exception:
            continue
        res = mk.get("result")
        if res in ("yes", "no"):
            print("[REC-END] " + json.dumps({"k": ticker, "c": coin, "r": res}, separators=(",", ":")))
            _done.add(ticker)
            _open.pop(ticker, None)
        elif now > close_at + 1800:
            _open.pop(ticker, None)
