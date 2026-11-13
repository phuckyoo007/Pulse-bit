"""
COMMODITY SCALP (13 Nov 15:17 CT, Matt): "open up bidding in commodities with a strategy you develop with the commodities
edge board using sigma values -- not just the win at the end, mainly buy in when the price is moving upward and exit
immediately with more."

Kalshi's 15-minute commodity markets (gold, silver, oil, natural gas, copper, platinum, palladium, Brent).
Every MKS_POLL_SECONDS it reads all of this round's commodity markets in one batched call and, for each side:

  ENTRY (all must be true)
    * MKS_MIN_SECONDS_LEFT..MKS_MAX_SECONDS_LEFT left in the round
    * our side's buy price is MKS_MIN_PRICE..MKS_MAX_PRICE and the book is tight (spread <= MKS_MAX_SPREAD)
    * "moving upward": our side's price is up MKS_RISE_CENTS+ over the last MKS_RISE_POLLS reads, and never dipped
    * sigma (Yahoo spot + 1-minute volatility vs the strike, from the Markets edge board): our side >= MKS_MIN_SIGMA
      and RISING over the same reads -- the underlying commodity really is moving our way, not just the Kalshi book
    * "the win at the end matters": the win chance from sigma is at least MKS_MIN_EDGE above the price we pay
  EXIT (first that fires) -- 13 Nov 15:26 CT (Matt): "ride it until it slows and stops"
    * TURNED: our side's sell price drops MKS_TRAIL_CENTS under its best since we bought
    * STALLED: ahead of what we paid but no new high for MKS_STALL_SECONDS
    * STOP: our side's sell price is MKS_STOP_CENTS under what we paid, or sigma drops under MKS_EXIT_SIGMA
    * TIME: under MKS_HOLD_FINAL_SECONDS left -> sell, unless it's a near-sure winner (bid >= 92c and sigma >= 2): hold
  1 share, at most MKS_MAX_OPEN open, one entry per market per round, stops for the day after MKS_DAY_LOSS_LIMIT.

MKS_PAPER = True: nothing is sent to Kalshi -- buys are simulated at the real ask, sells at the real bid, real fees.
Logged as [MKSCALP] / [MKSCALP-PAPER].
"""
import math
import time
import uuid
from datetime import datetime, timezone, timedelta

import requests

import markets_paper as mk

# 13 Nov 15:23 CT (Matt shared it): the Markets Edge Board's own sigma + fitted win % (spot, volatility, momentum,
# order book), read from its JSON. Falls back to Pulse's own Yahoo sigma if the board can't be reached.
BOARD_URL = "https://markets-board-production.up.railway.app/api/state"
_board = {"at": 0.0, "rows": {}, "warned": 0.0}


def _board_rows():
    if time.time() - _board["at"] < 3:
        return _board["rows"]
    _board["at"] = time.time()
    try:
        d = requests.get(BOARD_URL, timeout=4).json()
        _board["rows"] = {r.get("ticker"): r for r in d.get("markets", []) if r.get("ticker")}
    except Exception as e:
        if time.time() - _board["warned"] > 300:
            _board["warned"] = time.time()
            print(f"[MKSCALP] Markets Edge Board unreachable ({e}) -- using Pulse's own sigma for now.")
        _board["rows"] = {}
    return _board["rows"]


def _sig_win(ticker, m, side, sl):
    """(sigma for our side, win chance for our side) -- from the edge board when it has the market."""
    r = _board_rows().get(ticker)
    if r and r.get("z") is not None and r.get("dir") in (1, -1):
        z_yes = float(r["z"]) * (1 if r["dir"] == 1 else -1)
        p_yes = r.get("model_p")
        zs = z_yes if side == "yes" else -z_yes
        win = None if p_yes is None else (float(p_yes) if side == "yes" else 1 - float(p_yes))
        return zs, (win if win is not None else _phi(zs)), "board"
    zs = mk._held_sigma(ticker, m, side, sl)
    return zs, (None if zs is None else _phi(zs)), "pulse"

MKS_ENABLED = True
MKS_PAPER = False           # 13 Nov 15:27 CT (Matt): run it live at 1 share
MKS_SHARES = 1
MKS_POLL_SECONDS = 4
MKS_MAX_SECONDS_LEFT = 720  # 12:00 left
MKS_MIN_SECONDS_LEFT = 120  # 2:00 left
MKS_MIN_PRICE = 0.30
MKS_MAX_PRICE = 0.85
MKS_MAX_SPREAD = 0.04
MKS_RISE_POLLS = 3          # ~12 seconds of reads
MKS_RISE_CENTS = 0.02
MKS_MIN_SIGMA = 0.3
MKS_MIN_EDGE = 0.03
MKS_TAKE_PROFIT_CENTS = None   # 13 Nov 15:26 CT (Matt): no fixed take-profit -- "ride it until it slows and stops"
MKS_TRAIL_CENTS = 0.02         # sell once our side's sell price is 2c under its best since we bought (it turned)
MKS_STALL_SECONDS = 24         # ...or it hasn't made a new high for 24s (it stopped climbing) while we're ahead
MKS_STOP_CENTS = 0.06
MKS_EXIT_SIGMA = 0.0
MKS_HOLD_FINAL_SECONDS = 60
MKS_MAX_OPEN = 3
MKS_DAY_LOSS_LIMIT = 3.00
# 13 Nov 15:28 CT (Matt): "run it live -- the first time it loses, trip a circuit breaker, then we'll work on it".
# First losing trade -> no new commodity bets (open ones still get their exits) until Matt restarts it.
MKS_BREAKER = True
MKS_TRIPPED = True    # 13 Nov 15:33 CT: tripped -- NATGAS NO 77c -> 73c in 16s, -$0.08. Off until Matt restarts it.

_round = {"key": None, "tickers": [], "last_poll": 0.0}
_hist = {}      # (ticker, side) -> [(t, ask, bid, sigma)]
_open = {}      # ticker -> {"side", "entry", "fee_in", "count", "at"}
_done = set()   # tickers already entered this round
_tot = {"n": 0, "won": 0, "pnl": 0.0, "day": None, "day_pnl": 0.0, "tripped": False}


def owned() -> set:
    return set(_open) if not MKS_PAPER else set()


def _tag():
    return "[MKSCALP-PAPER]" if MKS_PAPER else "[MKSCALP]"


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _today():
    return (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%d")


def _discover(client):
    """This round's commodity 15-minute markets near the money (either side 10-90c)."""
    out = []
    tables = dict(mk._EXTRA_PREFIXES)
    try:
        tables.update(mk._series_from_list(client))
    except Exception:
        pass
    for prefix in tables:
        if "15M" not in prefix.upper():
            continue
        try:
            now = time.time()
            for m in client.get_markets(status=None, series_ticker=prefix, limit=200, min_close_ts=now,
                                        max_close_ts=now + 16 * 60).get("markets", []):
                if m.get("status") and m.get("status") not in ("active", "open"):
                    continue
                if not mk._is_commodity(m.get("ticker", "")):
                    continue
                ya, yb = mk._f(m.get("yes_ask_dollars")), mk._f(m.get("yes_bid_dollars"))
                if ya is None or yb is None or not (0.10 <= ya <= 0.90):
                    continue
                out.append(m["ticker"])
        except Exception:
            continue
    return out


def _close(client, place_exit, ticker, pos, value, why):
    fee_out = mk._fee(pos["count"], value)
    if not MKS_PAPER:
        try:
            r = place_exit(client, ticker, "ask" if pos["side"] == "yes" else "bid", pos["count"], dry_run=False,
                           entry_price=pos["entry"])
        except Exception as e:
            print(f"[MKSCALP] {ticker}: sell failed ({e}) -- retrying next pass.")
            return
        if isinstance(r, dict) and r.get("skipped"):
            _open.pop(ticker, None)
            return
        if not (isinstance(r, dict) and (r.get("_sold") or 0) > 1e-9):
            return
        value = r.get("_real_exit_px", value)
        fee_out = mk._fee(pos["count"], value)
    pnl = (value - pos["entry"]) * pos["count"] - pos["fee_in"] - fee_out
    _tot["n"] += 1
    _tot["won"] += 1 if pnl > 0 else 0
    _tot["pnl"] += pnl
    if _tot["day"] != _today():
        _tot["day"], _tot["day_pnl"] = _today(), 0.0
    _tot["day_pnl"] += pnl
    print(f"{_tag()} EXIT {ticker} ({mk._label(ticker)}) {pos['side'].upper()}: {why} -- bought {pos['entry']*100:.0f}c, "
          f"sold {value*100:.0f}c after {time.time() - pos['at']:.0f}s, P/L {pnl:+.2f} | total {_tot['n']} trades, "
          f"{_tot['won']} up, {_tot['pnl']:+.2f} (today {_tot['day_pnl']:+.2f})")
    if MKS_BREAKER and pnl < -0.005 and not _tot["tripped"]:
        _tot["tripped"] = True
        print(f"[MKSCALP] CIRCUIT BREAKER TRIPPED -- first losing trade ({ticker} {pnl:+.2f}). No new commodity bets until "
              f"Matt restarts it; open ones keep their exits.")
    _open.pop(ticker, None)


def _settle(ticker, pos, m):
    res = m.get("result")
    won = res == pos["side"]
    pnl = ((1.0 if won else 0.0) - pos["entry"]) * pos["count"] - pos["fee_in"]
    _tot["n"] += 1
    _tot["won"] += 1 if pnl > 0 else 0
    _tot["pnl"] += pnl
    if _tot["day"] != _today():
        _tot["day"], _tot["day_pnl"] = _today(), 0.0
    _tot["day_pnl"] += pnl
    print(f"{_tag()} SETTLED {ticker} ({mk._label(ticker)}) {pos['side'].upper()}: {'WON' if won else 'LOST'} -- bought "
          f"{pos['entry']*100:.0f}c, P/L {pnl:+.2f} | total {_tot['n']} trades, {_tot['won']} up, {_tot['pnl']:+.2f}")
    if MKS_BREAKER and pnl < -0.005 and not _tot["tripped"]:
        _tot["tripped"] = True
        print(f"[MKSCALP] CIRCUIT BREAKER TRIPPED -- first losing trade ({ticker} {pnl:+.2f}). No new commodity bets until "
              f"Matt restarts it; open ones keep their exits.")
    _open.pop(ticker, None)


def _buy_live(client, ticker, side, cost):
    import flip_star
    yes_price = cost if side == "yes" else round(1 - cost, 2)
    try:
        client.create_order(ticker=ticker, client_order_id=f"pulse-mkscalp-{uuid.uuid4()}",
                            side="bid" if side == "yes" else "ask", count=str(MKS_SHARES),
                            price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
    except Exception as e:
        print(f"[MKSCALP] {ticker}: buy failed ({e}).")
        return 0, None
    for _ in range(4):
        try:
            held, exposure = flip_star._held_count(client, ticker)
        except Exception:
            held, exposure = 0.0, 0.0
        if abs(held) > 1e-9:
            return abs(held), (exposure / abs(held) if exposure > 0 else cost)
        time.sleep(0.4)
    return 0, None


def run(client, place_exit=None):
    if not MKS_ENABLED or (not MKS_PAPER and place_exit is None):
        return
    now = time.time()
    if now - _round["last_poll"] < MKS_POLL_SECONDS:
        return
    _round["last_poll"] = now
    utc = datetime.now(timezone.utc)
    rkey = utc.strftime("%Y%m%d%H") + str(utc.minute // 15)
    if _round["key"] != rkey and (utc.minute % 15) >= 2:
        _round["key"] = rkey
        _round["tickers"] = _discover(client)
        _done.clear()
        for k in [k for k in _hist if k[0] not in _round["tickers"]]:
            _hist.pop(k, None)
        if _round["tickers"]:
            print(f"{_tag()} round {utc.strftime('%H:%M')} UTC: watching {len(_round['tickers'])} commodity market(s): "
                  f"{', '.join(sorted({mk._label(t) for t in _round['tickers']}))}")
    watch = set(_round["tickers"]) | set(_open)
    if not watch:
        return
    q = mk._batch(client, sorted(watch))

    # ---- exits ----
    for ticker, pos in list(_open.items()):
        m = q.get(ticker)
        if not m:
            continue
        if m.get("result") in ("yes", "no"):
            _settle(ticker, pos, m)
            continue
        sl = mk._secs_left(m)
        if sl is None or sl <= 0:
            continue
        ya, yb = mk._f(m.get("yes_ask_dollars")), mk._f(m.get("yes_bid_dollars"))
        bid = yb if pos["side"] == "yes" else (None if ya is None else 1 - ya)
        if bid is None:
            continue
        zs, _w, _src = _sig_win(ticker, m, pos["side"], sl)
        why = None
        if bid > pos.get("peak", pos["entry"]) + 1e-9:
            pos["peak"], pos["peak_at"] = bid, now
        peak = pos.get("peak", pos["entry"])
        if peak > pos["entry"] + 1e-9 and bid <= peak - MKS_TRAIL_CENTS + 1e-9:
            why = f"turned down -- {bid*100:.0f}c is {(peak - bid)*100:.0f}c under its high of {peak*100:.0f}c"
        elif bid > pos["entry"] + 1e-9 and now - pos.get("peak_at", pos["at"]) >= MKS_STALL_SECONDS:
            why = f"stalled -- no new high for {now - pos.get('peak_at', pos['at']):.0f}s (high {peak*100:.0f}c)"
        elif bid <= pos["entry"] - MKS_STOP_CENTS + 1e-9:
            why = f"stop (-{(pos['entry'] - bid)*100:.0f}c)"
        elif zs is not None and zs < MKS_EXIT_SIGMA:
            why = f"sigma turned against us ({zs:.2f})"
        elif sl < MKS_HOLD_FINAL_SECONDS and not (bid >= 0.92 and zs is not None and zs >= 2.0):
            why = f"{sl:.0f}s left -- out before the finish"
        if why:
            _close(client, place_exit, ticker, pos, bid, why)

    # ---- entries ----
    if MKS_TRIPPED or _tot["tripped"]:
        return   # circuit breaker: first loss -> no new bets
    if _tot["day"] == _today() and _tot["day_pnl"] <= -MKS_DAY_LOSS_LIMIT:
        return
    for ticker in _round["tickers"]:
        m = q.get(ticker)
        if not m:
            continue
        sl = mk._secs_left(m)
        ya, yb = mk._f(m.get("yes_ask_dollars")), mk._f(m.get("yes_bid_dollars"))
        if sl is None or ya is None or yb is None or ya <= 0 or yb <= 0:
            continue
        for side in ("yes", "no"):
            ask = ya if side == "yes" else 1 - yb
            bid = yb if side == "yes" else 1 - ya
            zs, win, src = _sig_win(ticker, m, side, sl)
            h = _hist.setdefault((ticker, side), [])
            h.append((now, ask, bid, zs))
            del h[:-(MKS_RISE_POLLS + 1)]
            if ticker in _open or ticker in _done or len(_open) >= MKS_MAX_OPEN:
                continue
            if not (MKS_MIN_SECONDS_LEFT <= sl <= MKS_MAX_SECONDS_LEFT):
                continue
            if not (MKS_MIN_PRICE <= ask <= MKS_MAX_PRICE) or ask - bid > MKS_MAX_SPREAD + 1e-9:
                continue
            if len(h) < MKS_RISE_POLLS + 1 or any(x[3] is None for x in h):
                continue
            asks = [x[1] for x in h]
            sig = [x[3] for x in h]
            rising = asks[-1] - asks[0] >= MKS_RISE_CENTS - 1e-9 and all(asks[i + 1] >= asks[i] - 1e-9 for i in range(len(asks) - 1))
            sig_up = sig[-1] >= MKS_MIN_SIGMA and sig[-1] > sig[0] + 0.05
            edge = (win if win is not None else _phi(sig[-1])) - ask
            if not (rising and sig_up and edge >= MKS_MIN_EDGE - 1e-9):
                continue
            fee_in = mk._fee(MKS_SHARES, ask)
            entry, count = ask, MKS_SHARES
            if not MKS_PAPER:
                count, entry = _buy_live(client, ticker, side, ask)
                if count <= 0:
                    _done.add(ticker)
                    print(f"[MKSCALP] {ticker}: nothing filled at {ask*100:.0f}c.")
                    continue
                fee_in = mk._fee(count, entry)
            _open[ticker] = {"side": side, "entry": entry, "fee_in": fee_in, "count": count, "at": now,
                             "peak": bid, "peak_at": now}
            _done.add(ticker)
            print(f"{_tag()} ENTRY {ticker} ({mk._label(ticker)}) {side.upper()} {count:g} @ {entry*100:.0f}c: price up "
                  f"{(asks[-1]-asks[0])*100:.0f}c in {now - h[0][0]:.0f}s, sigma {sig[0]:.2f} -> {sig[-1]:.2f} "
                  f"(win {(win if win is not None else _phi(sig[-1]))*100:.0f}% from {src}), {sl:.0f}s left")
