"""
TICKRIDE -- PAPER ONLY (10 Nov 16:42 CT, Matt: "when we get on the ride it better be going up every single time and we
have to get off the second it stops").

The board recorder only sees prices every ~13s, which is too slow to judge this. This module listens to Kalshi's live
price stream (every bid/ask change) for the current BTC and XRP 15-minute rounds and:

  1. logs the prices ([TICK] lines, at most TICK_LOG_PER_SEC per market per second) so rules can be re-tested later;
  2. paper-trades three versions of the ride rule, TICKRIDE_SHARES each, never placing a real order:
       TAKER   -- buy at the ask once a side's price has risen RISES_NEEDED changes in a row within RISE_WINDOW seconds;
                  sell at the bid the moment the bid comes in below its high since we bought (it stopped rising),
                  or once it hasn't made a new high for STALL_SECONDS.
       MAKER   -- same, but assume we bought at the bid with a resting order (cheaper fee, no spread). Optimistic:
                  a resting order wouldn't always fill.
       ROOM2   -- like TAKER, but only sells once the bid is 2c under its high (gives the ride a little room).
"""
import json
import math
import threading
import time

import flip_star
import kalshi_ws

TICKRIDE_ENABLED = False   # 11 Nov 00:00 CT (Matt): dormant -- only Follow the crowd (LATE80) runs
TICKRIDE_COINS = {"BTC", "XRP"}
TICKRIDE_SHARES = 2
RISES_NEEDED = 3
RISE_WINDOW = 5.0
STALL_SECONDS = 3.0
MIN_PRICE, MAX_PRICE = 0.15, 0.85
MIN_SECONDS_LEFT = 60
TICK_LOG_PER_SEC = 4
VARIANTS = {"TAKER": {"maker": False, "room": 0.0},
            "MAKER": {"maker": True, "room": 0.0},
            "ROOM2": {"maker": False, "room": 0.02}}

_lock = threading.Lock()
_close = {}       # ticker -> close time
_hist = {}        # (ticker, side) -> [(at, price we'd pay)]
_last = {}        # ticker -> (yb, ya)
_pos = {}         # (variant, ticker) -> dict
_tot = {v: {"n": 0, "won": 0, "pnl": 0.0} for v in VARIANTS}
_logged = {}      # ticker -> [second, count]
_started = [False]


def _fee(p, n, maker=False):
    rate = 0.0175 if maker else 0.07
    return math.ceil(rate * p * (1 - p) * n * 100 - 1e-9) / 100


def _sides(yb, ya):
    """{side: (buy price, sell price)}"""
    return {"yes": (ya, yb), "no": (1 - yb, 1 - ya)}


def _close_pos(v, ticker, p, sell, why, at):
    n = TICKRIDE_SHARES
    cfg = VARIANTS[v]
    pnl = (sell - p["entry"]) * n - _fee(p["entry"], n, cfg["maker"]) - _fee(sell, n)
    t = _tot[v]
    t["n"] += 1
    t["won"] += 1 if pnl > 0 else 0
    t["pnl"] += pnl
    print(f"[TICKRIDE-PAPER] {v} EXIT {ticker} {p['side'].upper()} {n} @ {sell:.2f} (bought {p['entry']:.2f}, high "
          f"{p['peak']:.2f}, {at - p['at']:.1f}s, {why}) {pnl:+.2f} -- {v} total {t['n']} rides, {t['won']} up, "
          f"{t['pnl']:+.2f}")


def _on_tick(ticker, yb, ya, at):
    if ticker not in _close or yb is None or ya is None or ya <= 0 or yb <= 0:
        return
    with _lock:
        lg = _logged.setdefault(ticker, [0, 0])
        sec = int(at)
        if lg[0] != sec:
            lg[0], lg[1] = sec, 0
        if lg[1] < TICK_LOG_PER_SEC and _last.get(ticker) != (yb, ya):
            lg[1] += 1
            print("[TICK] " + json.dumps({"k": ticker, "t": round(at, 2), "yb": round(yb, 3), "ya": round(ya, 3)},
                                         separators=(",", ":")))
        _last[ticker] = (yb, ya)
        tl = _close[ticker] - at
        for side, (buy, sell) in _sides(yb, ya).items():
            h = _hist.setdefault((ticker, side), [])
            if not h or h[-1][1] != buy:
                h.append((at, buy))
            while len(h) > RISES_NEEDED + 1:
                h.pop(0)
            # exits first
            for v, cfg in VARIANTS.items():
                p = _pos.get((v, ticker))
                if not p or p["side"] != side:
                    continue
                if sell > p["peak"] + 1e-9:
                    p["peak"], p["peak_at"] = sell, at
                elif sell < p["peak"] - cfg["room"] - 1e-9:
                    _close_pos(v, ticker, _pos.pop((v, ticker)), sell, "stopped rising", at)
            # entries
            rising = (len(h) == RISES_NEEDED + 1 and all(h[i + 1][1] > h[i][1] + 1e-9 for i in range(RISES_NEEDED))
                      and at - h[0][0] <= RISE_WINDOW)
            if not rising or tl < MIN_SECONDS_LEFT or not (MIN_PRICE <= buy <= MAX_PRICE):
                continue
            for v, cfg in VARIANTS.items():
                if (v, ticker) in _pos:
                    continue
                entry = sell if cfg["maker"] else buy
                _pos[(v, ticker)] = {"side": side, "entry": entry, "peak": sell, "peak_at": at, "at": at}
                print(f"[TICKRIDE-PAPER] {v} ENTRY {ticker} {side.upper()} {TICKRIDE_SHARES} @ {entry:.2f} "
                      f"(rose {RISES_NEEDED} times in {at - h[0][0]:.1f}s, {int(tl)}s left)")


def run() -> None:
    """Main-loop hook: pick this round's BTC/XRP 15-minute markets, subscribe them, and time out stalled rides."""
    if not TICKRIDE_ENABLED:
        return
    if not _started[0]:
        _started[0] = True
        kalshi_ws.add_listener(_on_tick)
        print("[TICKRIDE-PAPER] started -- paper only, no real orders.")
    try:
        b = flip_star.board() or {}
    except Exception:
        b = {}
    now = time.time()
    want = {}
    for ticker, n in b.items():
        if "15M" in ticker.split("-")[0].upper() and n.get("coin") in TICKRIDE_COINS and n.get("tl") is not None:
            want[ticker] = now + float(n["tl"])
    with _lock:
        for t, c in want.items():
            _close[t] = c
        # round over: close anything still open at the last price, forget old markets
        for (v, t), p in list(_pos.items()):
            last = _last.get(t)
            stalled = now - p["peak_at"] >= STALL_SECONDS
            ended = now >= _close.get(t, now) - 2
            if last and (stalled or ended):
                sell = _sides(*last)[p["side"]][1]
                _close_pos(v, t, _pos.pop((v, t)), sell, "no new high for 3s" if stalled else "round ending", now)
        for t in [t for t, c in _close.items() if now > c + 60]:
            _close.pop(t, None)
            _last.pop(t, None)
            _logged.pop(t, None)
            for s in ("yes", "no"):
                _hist.pop((t, s), None)
    kalshi_ws.watch_extra(want.keys())
