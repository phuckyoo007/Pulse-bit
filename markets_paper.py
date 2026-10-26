"""
PULSE MARKETS -- PAPER TRIAL for Kalshi's non-crypto 15-minute AND hourly markets (commodities like natural gas / gold / silver,
FX like EUR/USD, stock indexes). NO REAL ORDERS: this module has no order code at all. Log prefix [MKTS-PAPER].

Runs inside bot.py next to live Pulse, using Pulse's current crypto rules as the starting point:
  ENTRY  last MK_MAX_SECONDS_LEFT seconds of the round, favored side priced MK_MIN_PRICE-MK_MAX_PRICE,
         tight book (spread <= MK_MAX_SPREAD), one bet per market, at most MK_MAX_OPEN open, MK_SHARES shares.
         (No price model: there is no working commodity/FX price feed from Railway, so these bet on price alone.)
  EXIT   our side <= MK_STOP (80c) -> sell; last 45s the line is MK_LAST_WINDOW_STOP (70c); otherwise hold to settlement.
  AFTER  every paper sell is followed to settlement ("would have WON/LOST") so the stop can be judged.

Kalshi API load is kept small: one discovery pass per round (when the round enters its last 5 minutes), then ONE
batched /markets?tickers=... read every MK_POLL_SECONDS for the handful of live markets and open paper bets.
"""
import json
import math
import os
import time
from datetime import datetime, timezone

import strategy

MK_ENABLED = True
MK_PAPER = True                    # MUST stay True -- there is no live-order code in this module
MK_START_BALANCE = 40.0
MK_SHARES = 15
MK_MAX_OPEN = 4
MK_MAX_SECONDS_LEFT = 240
MK_MIN_SECONDS_LEFT = 20
MK_MIN_PRICE = 0.94
MK_MAX_PRICE = 0.98
MK_MAX_SPREAD = 0.03
MK_STOP = 0.80
MK_LAST_WINDOW_SECONDS = 45
MK_LAST_WINDOW_STOP = 0.70
MK_POLL_SECONDS = 5

# Every non-crypto 15-minute series Pulse knows about, plus a few likely ones (wrong guesses just come back empty).
_EXTRA_PREFIXES = {"KXNATGAS15M": "NATGAS", "KXWTI15M": "OIL", "KXOIL15M": "OIL", "KXBRENT15M": "BRENT"}

# Hourly + 15-minute commodity / currency series are also found from Kalshi's live series list (refreshed every 30 min).
_KEYWORDS = ("GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM", "NATURAL GAS", "NATGAS", "CRUDE", "OIL", "WTI", "BRENT",
             "EUR", "GBP", "JPY", "USD/", "/USD", "YEN", "EURO", "POUND", "DOLLAR", "CAD", "AUD", "CHF")
_series_cache = {"at": 0.0, "found": {}}
MK_ONE_PER_EVENT = True          # hourly events list many strike prices -- bet at most one per event

_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "markets_paper_state.json")
_s = {"balance": MK_START_BALANCE, "n": 0, "wins": 0, "pnl": 0.0, "by_kind": {}}
_open = {}            # ticker -> paper position
_tried = set()
_events_tried = set()
_aftermath = {}       # ticker -> {"side", "exit_value", "entry", "count"}
_round = {"key": None, "tickers": [], "last_poll": 0.0}

try:
    if os.path.exists(_STATE_FILE):
        _s.update(json.load(open(_STATE_FILE)))
except Exception:
    pass


def _save():
    try:
        json.dump(_s, open(_STATE_FILE, "w"))
    except Exception:
        pass


def _fee(count, p):
    return math.ceil(0.07 * count * p * (1 - p) * 100 - 1e-9) / 100


def _f(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None


def _secs_left(m):
    ct = m.get("close_time")
    if not ct:
        return None
    try:
        return (datetime.fromisoformat(str(ct).replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return None


def _label(ticker):
    series = ticker.split("-")[0]
    for table in (strategy.COMMODITY_SERIES_PREFIXES, strategy.FX_SERIES_PREFIXES, strategy.INDEX_SERIES_PREFIXES, _EXTRA_PREFIXES,
                  _series_cache["found"]):
        if series in table:
            return table[series]
    return series


def _series_from_list(client):
    """Commodity / currency series with an hourly or 15-minute schedule, from Kalshi's live series list."""
    if time.time() - _series_cache["at"] < 1800:
        return _series_cache["found"]
    _series_cache["at"] = time.time()
    found = {}
    try:
        for srs in client.get_series(limit=1000).get("series", []):
            tk = str(srs.get("ticker", ""))
            text = f"{tk} {srs.get('title', '')}".upper()
            freq = str(srs.get("frequency", "")).lower()
            cat = str(srs.get("category", "")).upper()
            if "CRYPTO" in cat or tk.upper().startswith(tuple(f"KX{c}" for c in ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE", "ZEC", "NEAR", "BCH", "SUI"))):
                continue
            fast = ("hour" in freq) or ("15" in freq) or ("min" in freq) or tk.upper().endswith(("15M", "H"))
            if fast and any(k in text for k in _KEYWORDS):
                found[tk] = (srs.get("title") or tk)[:30]
        if found:
            print(f"[MKTS-PAPER] series list: {len(found)} hourly/15-min commodity & currency series -- {', '.join(sorted(found))[:300]}")
    except Exception as e:
        print(f"[MKTS-PAPER] couldn't read Kalshi's series list ({e}) -- using the known 15-minute series only.")
    _series_cache["found"] = found
    return found


def _discover(client):
    """Once per round: find this round's open non-crypto 15-minute markets."""
    tickers = []
    tables = {**strategy.COMMODITY_SERIES_PREFIXES, **strategy.FX_SERIES_PREFIXES, **strategy.INDEX_SERIES_PREFIXES, **_EXTRA_PREFIXES}
    try:
        strategy.discover_dynamic_series(client)   # picks up any real 15M commodity/FX series (cached 15 min)
        tables.update(strategy.COMMODITY_SERIES_PREFIXES)
        tables.update(strategy.FX_SERIES_PREFIXES)
    except Exception:
        pass
    tables.update(_series_from_list(client))
    for prefix in tables:
        try:
            for m in client.get_markets(status="open", series_ticker=prefix, limit=100).get("markets", []):
                sl = _secs_left(m)
                if sl is None or not (0 < sl <= 15 * 60 + 30):
                    continue
                ya, yb = _f(m.get("yes_ask_dollars")), _f(m.get("yes_bid_dollars"))
                fav = max(ya or 0, 1 - (yb if yb is not None else 1))
                if 0.80 <= fav <= 0.995:          # only strikes that could reach the 94-98c buy band
                    tickers.append(m["ticker"])
        except Exception:
            continue
    return tickers


def _batch(client, tickers):
    if not tickers:
        return {}
    out = {}
    tickers = list(tickers)
    for i in range(0, len(tickers), 40):
        chunk = tickers[i:i + 40]
        try:
            resp = client._request("GET", "/markets", params={"tickers": ",".join(chunk), "limit": len(chunk)})
            out.update({m.get("ticker"): m for m in resp.get("markets", [])})
        except Exception as e:
            print(f"[MKTS-PAPER] batch quote read failed ({e}) -- trying again next pass.")
    return out


def _close(ticker, pos, value, why, settled):
    fee_out = 0.0 if settled else _fee(pos["count"], value)
    pnl = (value - pos["entry"]) * pos["count"] - pos["fee_in"] - fee_out
    _s["balance"] += value * pos["count"] - fee_out
    _s["n"] += 1
    _s["wins"] += 1 if pnl > 0 else 0
    _s["pnl"] += pnl
    k = _s["by_kind"].setdefault(pos["label"], [0, 0, 0.0])
    k[0] += 1
    k[1] += 1 if pnl > 0 else 0
    k[2] += pnl
    print(f"[MKTS-PAPER] EXIT {ticker} ({pos['label']}): {why} -- {pos['count']} @ ${pos['entry']:.2f} -> ${value:.2f}, "
          f"pnl ${pnl:+.2f} | paper balance ${_s['balance']:.2f} | trial: {_s['n']} bets, {_s['wins']} wins, ${_s['pnl']:+.2f}")
    _open.pop(ticker, None)
    if not settled:
        _aftermath[ticker] = {"side": pos["side"], "exit_value": value, "entry": pos["entry"], "count": pos["count"]}
    _save()


def run(client):
    """One pass. Never sends an order."""
    if not MK_ENABLED or not MK_PAPER:
        return
    now = time.time()
    if now - _round["last_poll"] < MK_POLL_SECONDS:
        return
    _round["last_poll"] = now
    utc = datetime.now(timezone.utc)
    rkey = utc.strftime("%Y%m%d%H") + str(utc.minute // 15)
    in_window = (utc.minute % 15) >= 10          # last 5 minutes of a round
    if in_window and _round["key"] != rkey:
        _round["key"], _round["tickers"] = rkey, _discover(client)
        if _round["tickers"]:
            print(f"[MKTS-PAPER] round {utc.strftime('%H:%M')} UTC: watching {len(_round['tickers'])} market(s): "
                  f"{', '.join(sorted({_label(t) for t in _round['tickers']}))}")
    watch = set(_open) | set(_aftermath) | (set(_round["tickers"]) if in_window else set())
    if not watch:
        return
    q = _batch(client, sorted(watch))

    # settlements + aftermath
    for ticker in list(_open):
        m = q.get(ticker)
        if not m:
            continue
        res = m.get("result")
        pos = _open[ticker]
        if res in ("yes", "no"):
            won = (pos["side"] == "yes") == (res == "yes")
            _close(ticker, pos, 1.0 if won else 0.0, f"settled {res.upper()} ({'won' if won else 'lost'})", True)
            continue
        sl = _secs_left(m)
        if sl is None or sl <= 0:
            continue                                    # closed, waiting for the result
        ya, yb = _f(m.get("yes_ask_dollars")), _f(m.get("yes_bid_dollars"))
        if ya is None or yb is None:
            continue
        value = yb if pos["side"] == "yes" else 1 - ya   # what selling our side would get right now
        line = MK_LAST_WINDOW_STOP if sl <= MK_LAST_WINDOW_SECONDS else MK_STOP
        if value <= line + 1e-9:
            _close(ticker, pos, value, f"stop (our side {value*100:.0f}c <= {line*100:.0f}c, {sl:.0f}s left)", False)
    for ticker in list(_aftermath):
        m = q.get(ticker)
        if not m or m.get("result") not in ("yes", "no"):
            continue
        a = _aftermath.pop(ticker)
        won = (a["side"] == "yes") == (m["result"] == "yes")
        diff = ((1.0 if won else 0.0) - a["exit_value"]) * a["count"]
        print(f"[MKTS-AFTERMATH] {ticker}: sold @ ${a['exit_value']:.2f} (entry ${a['entry']:.2f}); settled {m['result'].upper()} -> "
              f"held would have {'WON' if won else 'LOST'}; the sell {'COST' if diff > 0 else 'SAVED'} ${abs(diff):.2f}")

    # entries
    if not in_window:
        return
    for ticker in _round["tickers"]:
        if len(_open) >= MK_MAX_OPEN:
            break
        if ticker in _open or ticker in _tried:
            continue
        m = q.get(ticker)
        if m and MK_ONE_PER_EVENT and m.get("event_ticker") in _events_tried:
            continue
        if not m:
            continue
        sl = _secs_left(m)
        if sl is None or not (MK_MIN_SECONDS_LEFT <= sl <= MK_MAX_SECONDS_LEFT):
            continue
        ya, yb = _f(m.get("yes_ask_dollars")), _f(m.get("yes_bid_dollars"))
        if ya is None or yb is None or ya <= 0 or yb <= 0 or ya - yb > MK_MAX_SPREAD + 1e-9:
            continue
        no_ask = 1 - yb
        side, cost = ("yes", ya) if ya >= no_ask else ("no", no_ask)
        if not (MK_MIN_PRICE <= cost <= MK_MAX_PRICE):
            continue
        fee_in = _fee(MK_SHARES, cost)
        if _s["balance"] < cost * MK_SHARES + fee_in:
            continue
        _s["balance"] -= cost * MK_SHARES + fee_in
        _open[ticker] = {"side": side, "entry": cost, "count": MK_SHARES, "fee_in": fee_in, "label": _label(ticker),
                         "event": m.get("event_ticker")}
        _tried.add(ticker)
        if m.get("event_ticker"):
            _events_tried.add(m["event_ticker"])
        print(f"[MKTS-PAPER] ENTRY {ticker} ({_label(ticker)}): {side.upper()} {MK_SHARES} @ ${cost:.2f} (simulated), "
              f"{sl:.0f}s left | paper balance ${_s['balance']:.2f}")
        _save()
