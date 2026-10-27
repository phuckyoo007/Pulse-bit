"""
PULSE MARKETS -- Kalshi's non-crypto 15-minute AND hourly markets (commodities like natural gas / gold / silver,
FX like EUR/USD, stock indexes). 26 Oct 14:29 CT (Matthew): LIVE with 2-share bets (MK_PAPER = False).
Live orders are immediate-or-cancel buys; sells go through Pulse's own place_exit (spike guard, 20c instant floor).
Pulse's hard backstop and manual-bet adopter leave these positions alone (see owned()). Log prefix [MKTS].

Runs inside bot.py next to live Pulse, using Pulse's current crypto rules as the starting point:
  ENTRY  last MK_MAX_SECONDS_LEFT seconds of the round, favored side priced MK_MIN_PRICE-MK_MAX_PRICE,
         tight book (spread <= MK_MAX_SPREAD), one bet per market, at most MK_MAX_OPEN open, MK_SHARES shares.
         (No price model: there is no working commodity/FX price feed from Railway, so these bet on price alone.)
  EXIT   our side <= 80% of entry -> sell instantly, all round (20%-of-entry instant floor skips the spike wait);
         'To flip' sigma (Yahoo spot + 1m volatility vs the strike) <= 0.35 for 3 seconds -> sell; otherwise hold to settlement.
  AFTER  every paper sell is followed to settlement ("would have WON/LOST") so the stop can be judged.

Kalshi API load is kept small: one discovery pass per round (when the round enters its last 5 minutes), then ONE
batched /markets?tickers=... read every MK_POLL_SECONDS for the handful of live markets and open paper bets.
"""
import json
import math
import os
import time
from datetime import datetime, timezone

import requests

import strategy

MK_ENABLED = True
MK_PAPER = False                   # 26 Oct: live, 2 shares a bet (set True to go back to paper)
MK_START_BALANCE = 40.0
MK_SHARES = 5
MK_MAX_OPEN = 4
MK_MAX_SECONDS_LEFT = 180   # 26 Oct 19:17 CT (Matthew): last 3 minutes only
MK_MIN_SECONDS_LEFT = 20
MK_MIN_PRICE = 0.94
MK_MAX_PRICE = 0.98
MK_MAX_SPREAD = 0.03
MK_STOP = 0.80
MK_STOP_FRACTION = 0.80     # sell at 80% of entry (a 20% loss), instantly, any time in the round
MK_LAST_WINDOW_SECONDS = 45
MK_LAST_WINDOW_STOP = 0.80   # 26 Oct: same 80%-of-entry line in the last 45s
MK_POLL_SECONDS = 5
MK_RED_SIGMA = 0.17          # 26 Oct 19:21 CT (Matthew): sell only in RED 3 (<0.17 sigma), same as crypto Pulse ...
MK_RED_SECONDS = 0           # 26 Oct 19:32 CT (Matthew): no wait -- sell the moment it reads RED 3
MK_RED_POLL_SECONDS = 1      # poll every second while a bet is red, so the 3s is honoured
_YAHOO = [("NATGAS", "NG=F"), ("NATURAL GAS", "NG=F"), ("BRENT", "BZ=F"), ("WTI", "CL=F"), ("OIL", "CL=F"), ("CRUDE", "CL=F"),
          ("GOLD", "GC=F"), ("SILVER", "SI=F"), ("COPPER", "HG=F"), ("PLATINUM", "PL=F"), ("PALLADIUM", "PA=F"),
          ("EURUSD", "EURUSD=X"), ("GBPUSD", "GBPUSD=X"), ("AUDUSD", "AUDUSD=X"), ("USDJPY", "JPY=X"), ("USDCAD", "CAD=X"),
          ("SP500", "^GSPC"), ("INX", "^GSPC"), ("NASDAQ", "^NDX"), ("NDQ", "^NDX"), ("DOW", "^DJI"), ("DJIA", "^DJI")]
_yq = {}              # yahoo symbol -> {"at", "spot", "vol"}
_red_since = {}       # ticker -> when our side first read red

# Every non-crypto 15-minute series Pulse knows about, plus a few likely ones (wrong guesses just come back empty).
_EXTRA_PREFIXES = {   # 26 Oct: the real series names, from the Markets Edge Board's scan of Kalshi's full series list
    "KXNATGAS15M": "NATGAS", "KXWTI15M": "OIL", "KXWTIH": "OIL", "KXBRENT15M": "BRENT",
    "KXGOLD15M": "GOLD", "KXGOLDH": "GOLD", "KXSILVER15M": "SILVER", "KXSILVERH": "SILVER",
    "KXCOPPER15M": "COPPER", "KXPLATINUM15M": "PLATINUM", "KXPLATINUMH": "PLATINUM",
    "KXPALLADIUM15M": "PALLADIUM", "KXPALLADIUMH": "PALLADIUM",
    "KXEURUSD15M": "EURUSD", "KXEURUSDH": "EURUSD", "KXGBPUSD15M": "GBPUSD", "KXGBPUSDH": "GBPUSD",
    "KXUSDJPY15M": "USDJPY", "KXUSDJPYH": "USDJPY",
    "KXINX15M": "SP500", "KXNDQ15M": "NASDAQ", "KXDJIA15M": "DOW", "KXDJIAH": "DOW"}

# Hourly + 15-minute commodity / currency series are also found from Kalshi's live series list (refreshed every 30 min).
_KEYWORDS = ("GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM", "NATURAL GAS", "NATGAS", "CRUDE", "OIL", "WTI", "BRENT",
             "EUR", "GBP", "JPY", "USD/", "/USD", "YEN", "EURO", "POUND", "DOLLAR", "CAD", "AUD", "CHF")
_series_cache = {"at": 0.0, "found": {}}
MK_ONE_PER_EVENT = True          # hourly events list many strike prices -- bet at most one per event

_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "markets_paper_state.json")
_s = {"balance": MK_START_BALANCE, "n": 0, "wins": 0, "pnl": 0.0, "by_kind": {}}
_open = {}            # ticker -> paper position
_tried = set()
_buying = set()      # buy in flight -- Pulse must not adopt the ticker meanwhile
_events_tried = set()
_aftermath = {}       # ticker -> {"side", "exit_value", "entry", "count"}
_round = {"key": None, "tickers": [], "last_poll": 0.0}

try:
    if os.path.exists(_STATE_FILE):
        _s.update(json.load(open(_STATE_FILE)))
except Exception:
    pass


def owned() -> set:
    """Live tickers this module is managing -- Pulse's adopter and hard backstop must not touch them."""
    return (set(_open) | _buying) if not MK_PAPER else set()


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
            _now = time.time()
            for m in client.get_markets(status=None, series_ticker=prefix, limit=200, min_close_ts=_now,
                                        max_close_ts=_now + 16 * 60).get("markets", []):
                if m.get("status") and m.get("status") not in ("active", "open"):
                    continue
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
    _red_since.pop(ticker, None)
    fee_out = 0.0 if settled else _fee(pos["count"], value)
    pnl = (value - pos["entry"]) * pos["count"] - pos["fee_in"] - fee_out
    if MK_PAPER:
        _s["balance"] += value * pos["count"] - fee_out
    _s["n"] += 1
    _s["wins"] += 1 if pnl > 0 else 0
    _s["pnl"] += pnl
    k = _s["by_kind"].setdefault(pos["label"], [0, 0, 0.0])
    k[0] += 1
    k[1] += 1 if pnl > 0 else 0
    k[2] += pnl
    print(f"{_tag()} EXIT {ticker} ({pos['label']}): {why} -- {pos['count']} @ ${pos['entry']:.2f} -> ${value:.2f}, "
          f"pnl ${pnl:+.2f} | trial: {_s['n']} bets, {_s['wins']} wins, ${_s['pnl']:+.2f}")
    _open.pop(ticker, None)
    if not settled:
        _aftermath[ticker] = {"side": pos["side"], "exit_value": value, "entry": pos["entry"], "count": pos["count"]}
    _save()


def _tag():
    return "[MKTS-PAPER]" if MK_PAPER else "[MKTS]"


def _buy(client, ticker, side, cost):
    """Live IOC buy of MK_SHARES of `side` at up to `cost`. Returns (filled_count, avg_entry) or (0, None)."""
    import uuid
    import flip_star
    limit = min(MK_MAX_PRICE, round(cost, 2))
    yes_price = limit if side == "yes" else round(1 - limit, 2)
    try:
        client.create_order(ticker=ticker, client_order_id=f"pulse-mkts-{uuid.uuid4()}",
                            side="bid" if side == "yes" else "ask", count=str(MK_SHARES),
                            price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
    except Exception as e:
        print(f"[MKTS] {ticker}: buy order failed ({e}).")
        return 0, None
    for _ in range(4):
        try:
            held, exposure = flip_star._held_count(client, ticker)
        except Exception:
            held, exposure = 0.0, 0.0
        if abs(held) > 1e-9:
            return abs(held), (exposure / abs(held) if exposure > 0 else limit)
        time.sleep(0.4)
    return 0, None


def _yahoo_symbol(ticker):
    key = (_label(ticker) + " " + ticker.split("-")[0]).upper().replace("/", "")
    for word, sym in _YAHOO:
        if word in key:
            return sym
    return None


def _spot_vol(sym):
    """(spot, per-minute volatility of log returns) from Yahoo 1-minute bars, cached 4s. None if unavailable."""
    c = _yq.get(sym)
    if c and time.time() - c["at"] < 4:
        return c["spot"], c["vol"]
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"interval": "1m", "range": "1d"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        res = r.json()["chart"]["result"][0]
        _ts = res.get("timestamp") or []
        _cl = res["indicators"]["quote"][0]["close"]
        bars_ts = [(t, x) for t, x in zip(_ts, _cl) if x]
        closes = [x for _, x in bars_ts]
        spot = float(res["meta"].get("regularMarketPrice") or closes[-1])
        bars = closes[-61:]
        rets = [math.log(bars[i] / bars[i - 1]) for i in range(1, len(bars)) if bars[i - 1] > 0]
        if len(rets) < 15:
            return None
        mu = sum(rets) / len(rets)
        vol = math.sqrt(sum((x - mu) ** 2 for x in rets) / (len(rets) - 1))
        if vol <= 0:
            return None
        _yq[sym] = {"at": time.time(), "spot": spot, "vol": vol, "bars": bars_ts}
        return spot, vol
    except Exception as e:
        if not c or time.time() - c.get("warned", 0) > 300:
            print(f"[MKTS] Yahoo price for {sym} unavailable ({e}) -- red-sigma sell off for it, 80% stop still on.")
            _yq[sym] = {**(c or {"spot": None, "vol": None}), "at": 0, "warned": time.time()}
        return None


def _held_sigma(ticker, m, side, secs_left):
    """'To flip' sigma for OUR side (negative = crossed to the other side), or None if it can't be worked out."""
    if "15M" not in ticker.split("-")[0]:
        return None        # hourly: no open-price anchor to line Yahoo up with Kalshi -> 80% stop only
    sym = _yahoo_symbol(ticker)
    if not sym or not secs_left or secs_left <= 0:
        return None
    sv = _spot_vol(sym)
    if not sv:
        return None
    spot, vol = sv
    st = (m.get("strike_type") or "").lower()
    fl, cp = _f(m.get("floor_strike")), _f(m.get("cap_strike"))
    # 26 Oct 19:31 CT: Yahoo's price is not Kalshi's settlement price (futures basis, different FX source), which
    # gave 6 false RED readings in a row (our side still 93-98c, all 6 would have WON). For a 15-minute up/down market
    # the strike IS the price at the round's open, so shift Yahoo by (Yahoo at open - strike). Hourly markets have no
    # such anchor -> no red sell for them (80% stop only).
    strike = fl if st in ("greater", "greater_or_equal") else cp
    ct = m.get("close_time")
    bars = (_yq.get(sym) or {}).get("bars") or []
    if not (strike and ct and bars):
        return None
    try:
        open_ts = datetime.fromisoformat(str(ct).replace("Z", "+00:00")).timestamp() - 900
    except Exception:
        return None
    # close of the 1-minute bar that ends at the round's open (Yahoo stamps bars by their start time)
    at_open = None
    for t, x in bars:
        if t <= open_ts - 60 + 1:
            at_open = x
        else:
            break
    if at_open is None or abs(math.log(at_open / strike)) > 0.01:
        return None        # no bar for the open, or Yahoo is clearly a different price series -- don't trust it
    spot = spot - (at_open - strike)
    if spot <= 0:
        return None
    t = max(secs_left, 5) / 60.0
    if st in ("greater", "greater_or_equal") and fl:
        z_yes = math.log(spot / fl) / (vol * math.sqrt(t))
    elif st in ("less", "less_or_equal") and cp:
        z_yes = math.log(cp / spot) / (vol * math.sqrt(t))
    else:
        return None
    return z_yes if side == "yes" else -z_yes


def run(client, place_exit=None):
    """One pass. In paper mode nothing is ever sent; live mode buys IOC and sells via Pulse's place_exit."""
    if not MK_ENABLED:
        return
    if not MK_PAPER and place_exit is None:
        return
    now = time.time()
    if now - _round["last_poll"] < (MK_RED_POLL_SECONDS if _red_since else MK_POLL_SECONDS):
        return
    _round["last_poll"] = now
    utc = datetime.now(timezone.utc)
    rkey = utc.strftime("%Y%m%d%H") + str(utc.minute // 15)
    in_window = (utc.minute % 15) >= 10          # last 5 minutes of a round
    if in_window and _round["key"] != rkey:
        _round["key"], _round["tickers"] = rkey, _discover(client)
        if _round["tickers"]:
            print(f"{_tag()} round {utc.strftime('%H:%M')} UTC: watching {len(_round['tickers'])} market(s): "
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
        line = pos["entry"] * MK_STOP_FRACTION   # 26 Oct (Matthew): 80% of what was paid, all round
        why = None
        if value <= line + 1e-9:
            why = f"stop (our side {value*100:.0f}c <= {line*100:.0f}c, {sl:.0f}s left)"
        else:
            zs = _held_sigma(ticker, m, pos["side"], sl)
            if zs is not None and zs <= MK_RED_SIGMA + 1e-9:
                first = _red_since.setdefault(ticker, now)
                if now - first >= MK_RED_SECONDS:
                    why = f"red (To flip {zs:.2f} sigma <= {MK_RED_SIGMA} for {now - first:.0f}s, our side {value*100:.0f}c, {sl:.0f}s left)"
                else:
                    print(f"[MKTS] {ticker}: RED -- To flip {zs:.2f} sigma, {now - first:.0f}s of {MK_RED_SECONDS}s (our side {value*100:.0f}c).")
            else:
                if ticker in _red_since:
                    print(f"[MKTS] {ticker}: back out of red (To flip {('?' if zs is None else f'{zs:.2f}')} sigma) -- holding.")
                _red_since.pop(ticker, None)
        if why:
            if MK_PAPER:
                _close(ticker, pos, value, why, False)
            else:
                try:
                    r = place_exit(client, ticker, "ask" if pos["side"] == "yes" else "bid", pos["count"], dry_run=False, entry_price=pos["entry"])
                except Exception as e:
                    print(f"[MKTS] {ticker}: sell failed ({e}) -- retrying next pass.")
                    continue
                if isinstance(r, dict) and r.get("skipped"):
                    print(f"[MKTS] {ticker}: Kalshi shows nothing left to sell -- dropping it.")
                    _open.pop(ticker, None)
                    continue
                if isinstance(r, dict) and (r.get("_sold") or 0) > 1e-9:
                    _close(ticker, pos, r.get("_real_exit_px", value), why, False)
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
        count = MK_SHARES
        if MK_PAPER:
            fee_in = _fee(MK_SHARES, cost)
            if _s["balance"] < cost * MK_SHARES + fee_in:
                continue
            _s["balance"] -= cost * MK_SHARES + fee_in
        else:
            _tried.add(ticker)
            _buying.add(ticker)
            count, filled_px = _buy(client, ticker, side, cost)
            if count <= 0:
                _buying.discard(ticker)
                print(f"[MKTS] {ticker}: nothing filled at ${cost:.2f} (price moved).")
                continue
            cost, fee_in = filled_px, _fee(count, filled_px)
        _open[ticker] = {"side": side, "entry": cost, "count": count, "fee_in": fee_in, "label": _label(ticker),
                         "event": m.get("event_ticker")}
        _buying.discard(ticker)
        _tried.add(ticker)
        if m.get("event_ticker"):
            _events_tried.add(m["event_ticker"])
        print(f"{_tag()} ENTRY {ticker} ({_label(ticker)}): {side.upper()} {count:g} @ ${cost:.2f}"
              f"{' (simulated)' if MK_PAPER else ''}, {sl:.0f}s left")
        _save()
