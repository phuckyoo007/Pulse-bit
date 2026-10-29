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
import re
import math
import os
import time
from datetime import datetime, timezone

import requests

import strategy

MK_ENABLED = True
MK_NEW_BETS = True    # 27 Oct 13:03 CT (Matthew): add the 15-minute interest-rate markets -- back on, RATES ONLY (see MK_RATES_ONLY)
MK_RATES_ONLY = True  # currencies + commodities stay off; only Treasury-yield (5Y/10Y/30Y...) 15-minute markets are bid on
# (was) MK_NEW_BETS = False   # 27 Oct 08:43 CT (Matthew: "turn off everything that's losing") -- Pulse Markets was -$11.20 over 33 bets; no new bets, open ones still managed to the end
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
MK_STOP_FRACTION = 0.86     # 27 Oct 21:17 CT (Matthew): sell at 86% of entry, instantly, any time in the round
MK_LAST_WINDOW_SECONDS = 45
MK_LAST_WINDOW_STOP = 0.86   # (unused -- MK_STOP_FRACTION applies all round)
MK_POLL_SECONDS = 5
# 27 Oct 08:38 CT (Matthew: "make sure that does not happen ever again"). Since 26 Oct 20:10 CT commodities lost
# -$14.91 on 5 bets (natgas -$4.87 and gold -$4.72 flipped in the last seconds and settled at 0 with no stop
# possible; gold/silver gapped through the 80% stop) against about +$3.80 from 15 wins. Currencies were about even.
# Commodities (gold, silver, copper, platinum, palladium, natural gas, oil, Brent) are no longer bid on.
MK_NO_COMMODITIES = False   # 27 Oct 20:45 CT (Matthew): commodities back on, 2 shares each (MK_COMMOD_SHARES)
MK_COMMOD_SHARES = 2
_COMMODITY_WORDS = ("GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM", "NATGAS", "NATURAL GAS", "WTI", "OIL", "BRENT", "CRUDE", "TIN")


def _is_commodity(ticker):
    key = (ticker.split("-")[0] + " " + _label(ticker)).upper()
    return any(w in key for w in _COMMODITY_WORDS)


MK_MIN_SIGMA = 1.8           # bid only at To flip >= 1.8 sigma for our side (crypto Pulse's bar)
# 27 Oct 14:33 CT (Matthew): loosen the rules for the interest-rate (Treasury yield) markets only -- no bets had
# gone in since 13:03 at 94c / 1.8 sigma. Everything else keeps MK_MIN_PRICE / MK_MIN_SIGMA.
MK_RATE_MIN_PRICE = 0.90
MK_RATE_MIN_SIGMA = 1.5
MK_NEAR_MISS_FLOOR = 0.80    # near-miss log: only strikes whose favored side is at least 80c (skips the far-away strikes)
MK_NEAR_MISS_EVERY = 60.0    # ... at most once a minute per market


def _min_price(ticker):
    return MK_RATE_MIN_PRICE if _is_rate(ticker) else (MK_FX_MIN_PRICE if _is_fx(ticker) else MK_MIN_PRICE)


def _min_sigma(ticker):
    return MK_RATE_MIN_SIGMA if _is_rate(ticker) else (MK_FX_MIN_SIGMA if _is_fx(ticker) else MK_MIN_SIGMA)


# 27 Oct 17:58 CT (Matthew): currencies back on, with much stricter rules. Since the 1.8-sigma rule they went 14 wins /
# 1 loss but net -$0.13: the loss (EUR/USD, filled at 91c, 80s left, gapped to 45c) erased the 14 small wins.
MK_FX_ON = True
MK_FX_SHARES = 2              # 27 Oct 18:06 CT (Matthew): currencies at 2 shares (rates stay at MK_SHARES)
MK_FX_MIN_PRICE = 0.96        # only the safest prices (the loser was bought at 91c)
MK_FX_MIN_SIGMA = 2.5         # well clear of the flip line (rest of Pulse Markets: 1.8)
MK_FX_MAX_SECONDS = 120       # last 2 minutes only -- less time for a reversal
MK_FX_MAX_SPREAD = 0.02       # tight books only
MK_FX_MAX_OPEN = 1            # one currency bet at a time (EUR/GBP/AUD/CAD/JPY all move with the dollar)
MK_FX_DAILY_LOSSES = 1        # one losing currency bet -> no more currency bets until tomorrow (Central)
_FX_WORDS = ("EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "MXN", "YEN", "EURO", "POUND")


def _is_fx(ticker):
    if _is_rate(ticker) or _is_commodity(ticker):
        return False
    key = (ticker.split("-")[0] + " " + _label(ticker)).upper()
    return any(w in key for w in _FX_WORDS)


def _fx_today():
    from datetime import timedelta
    return datetime.now(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d")
MK_RED_SIGMA = 0.33          # 27 Oct 10:56 CT (Matthew): sell at RED 2 (<=0.33 sigma), same as crypto Pulse ...
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
             "EUR", "GBP", "JPY", "USD/", "/USD", "YEN", "EURO", "POUND", "DOLLAR", "CAD", "AUD", "CHF",
             "TREASURY", "YIELD", "T-NOTE", "T-BOND", "10Y", "30Y", "5Y", "2Y", "10-YEAR", "30-YEAR", "5-YEAR", "2-YEAR")
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


_notes = {}


def _note(key, msg, every=10.0):
    if time.time() - _notes.get(key + msg[:30], 0) >= every:
        _notes[key + msg[:30]] = time.time()
        print(msg)


def _close(ticker, pos, value, why, settled):
    _red_since.pop(ticker, None)
    fee_out = 0.0 if settled else _fee(pos["count"], value)
    pnl = (value - pos["entry"]) * pos["count"] - pos["fee_in"] - fee_out
    if MK_PAPER:
        _s["balance"] += value * pos["count"] - fee_out
    _s["n"] += 1
    _s["wins"] += 1 if pnl > 0 else 0
    _s["pnl"] += pnl
    if pnl < 0 and _is_fx(ticker):
        if _s.get("fx_day") != _fx_today():
            _s["fx_day"], _s["fx_day_losses"] = _fx_today(), 0
        _s["fx_day_losses"] = _s.get("fx_day_losses", 0) + 1
        print(f"[MKTS] currency bet lost -- no more currency bets today (rule: {MK_FX_DAILY_LOSSES} loss/day).")
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


def _buy(client, ticker, side, cost, shares=None):
    """Live IOC buy of MK_SHARES of `side` at up to `cost`. Returns (filled_count, avg_entry) or (0, None)."""
    import uuid
    import flip_star
    # 26 Oct (AUD/USD bought at 91c on a 95c quote, then went to 0): re-read the price right before buying and skip
    # if our side has dropped below the band since the quote -- never buy into a falling price.
    try:
        fm = client.get_market(ticker).get("market", {})
        fya, fyb = _f(fm.get("yes_ask_dollars")), _f(fm.get("yes_bid_dollars"))
        now_cost = fya if side == "yes" else (None if fyb is None else 1 - fyb)
        if now_cost is None or now_cost < _min_price(ticker) - 1e-9 or now_cost < cost - 1e-9:
            print(f"[MKTS] {ticker}: price fell before the buy ({cost:.2f} -> "
                  f"{'?' if now_cost is None else f'{now_cost:.2f}'}) -- not buying into a drop.")
            return 0, None
    except Exception:
        pass
    limit = min(MK_MAX_PRICE, round(cost, 2))
    yes_price = limit if side == "yes" else round(1 - limit, 2)
    try:
        client.create_order(ticker=ticker, client_order_id=f"pulse-mkts-{uuid.uuid4()}",
                            side="bid" if side == "yes" else "ask", count=str(shares or MK_SHARES),
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


def _rate_key(ticker):
    return re.sub(r"[\s\-/]", "", (_label(ticker) + " " + ticker.split("-")[0]).upper()).replace("YEAR", "Y")


def _is_rate(ticker):
    """27 Oct: Kalshi's 15-minute Treasury-yield markets (5Y / 10Y / 30Y ...)."""
    k = _rate_key(ticker)
    return "TREASURY" in k or "YIELD" in k or bool(re.search(r"(?<![0-9])(2|5|10|30)Y", k))


def _rate_symbol(ticker):
    """Yahoo's CBOE yield indexes -- quoted in percent, same units as Kalshi's targets (e.g. 4.99)."""
    k = _rate_key(ticker)
    for pat, sym in ((r"(?<![0-9])30Y", "^TYX"), (r"(?<![0-9])10Y", "^TNX"), (r"(?<![0-9])5Y", "^FVX")):
        if re.search(pat, k):
            return sym
    return None        # 2Y etc.: no clean Yahoo feed -> no sigma -> no bid


def _yahoo_symbol(ticker):
    if _is_rate(ticker):
        return _rate_symbol(ticker)
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


_RATE_FUT = ((r"(?<![0-9])30Y", "ZB=F"), (r"(?<![0-9])10Y", "ZN=F"), (r"(?<![0-9])5Y", "ZF=F"), (r"(?<![0-9])2Y", "ZT=F"))


def _rate_sigma(ticker, m, side, secs_left):
    """28 Oct 22:20 CT (Matt): Yahoo's yield indexes (^TNX etc.) read flat inside a round -> sigma came out 0.00 on
    every rate market all day, so nothing was ever bid. Use the Treasury FUTURE instead (ZT/ZF/ZN/ZB, live 1-minute
    bars): yield moves opposite to the future's price. A 15-minute market's strike is the yield at the round's open,
    so z = how far the future has moved since the open, in units of its own 1-minute volatility (duration cancels
    out). Yield UP = future DOWN."""
    k = _rate_key(ticker)
    sym = next((f for pat, f in _RATE_FUT if re.search(pat, k)), None)
    if not sym or not secs_left or secs_left <= 0:
        return None
    sv = _spot_vol(sym)
    if not sv:
        return None
    spot, vol = sv
    st = (m.get("strike_type") or "").lower()
    ct = m.get("close_time")
    bars = (_yq.get(sym) or {}).get("bars") or []
    if not (ct and bars):
        return None
    try:
        open_ts = datetime.fromisoformat(str(ct).replace("Z", "+00:00")).timestamp() - 900
    except Exception:
        return None
    at_open = None
    for t, x in bars:
        if t <= open_ts - 60 + 1:
            at_open = x
        else:
            break
    if not at_open or bars[-1][0] < time.time() - 300:
        return None        # no bar for the open, or the futures feed is stale -> no sigma -> no bid
    last = bars[-1][1]
    t = max(secs_left, 5) / 60.0
    move = -math.log(last / at_open) / (vol * math.sqrt(t))    # + = yield up since the open
    if st in ("greater", "greater_or_equal"):
        z_yes = move
    elif st in ("less", "less_or_equal"):
        z_yes = -move
    else:
        return None
    return z_yes if side == "yes" else -z_yes


def _held_sigma(ticker, m, side, secs_left):
    """'To flip' sigma for OUR side (negative = crossed to the other side), or None if it can't be worked out."""
    if "15M" not in ticker.split("-")[0]:
        return None        # hourly: no open-price anchor to line Yahoo up with Kalshi -> 80% stop only
    if _is_rate(ticker):
        return _rate_sigma(ticker, m, side, secs_left)
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
            try:
                m = client.get_market(ticker).get("market")   # 26 Oct: missing from the batch -> read it on its own
            except Exception:
                m = None
        if not m:
            _note(ticker, f"[MKTS] {ticker}: no quote from Kalshi this pass -- can't check the stop.")
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
        # 26 Oct (AUD/USD -$4.58): when nobody is bidding for our side Kalshi sends no price; the old code skipped the
        # bet entirely, so neither stop ever ran and it rode to zero. No buyers now = our side is worth 0 -> stop fires
        # and the sell keeps trying every pass (at any price) until someone takes it.
        mine = yb if pos["side"] == "yes" else (None if ya is None else 1 - ya)
        if mine is None or mine <= 0:
            _note(ticker, f"[MKTS] {ticker}: NOBODY BIDDING for our {pos['side'].upper()} -- treating it as 0 and trying to sell.")
            mine = 0.0
        value = mine   # what selling our side would get right now
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
    if not in_window or not MK_NEW_BETS:
        return
    for ticker in _round["tickers"]:
        if len(_open) >= MK_MAX_OPEN:
            break
        if ticker in _open or ticker in _tried:
            continue
        if MK_RATES_ONLY and not (_is_rate(ticker) or (MK_FX_ON and _is_fx(ticker))
                                  or (not MK_NO_COMMODITIES and _is_commodity(ticker))):
            continue   # 27 Oct: interest rates, plus currencies under the strict MK_FX_* rules
        if _is_fx(ticker):
            if _s.get("fx_day") == _fx_today() and _s.get("fx_day_losses", 0) >= MK_FX_DAILY_LOSSES:
                continue   # a currency bet already lost today
            if sum(1 for t in _open if _is_fx(t)) >= MK_FX_MAX_OPEN:
                continue
        if MK_NO_COMMODITIES and _is_commodity(ticker):
            continue   # 27 Oct: commodities off -- see MK_NO_COMMODITIES
        m = q.get(ticker)
        if m and MK_ONE_PER_EVENT and m.get("event_ticker") in _events_tried:
            continue
        if not m:
            continue
        sl = _secs_left(m)
        if sl is None or not (MK_MIN_SECONDS_LEFT <= sl <= MK_MAX_SECONDS_LEFT):
            continue
        ya, yb = _f(m.get("yes_ask_dollars")), _f(m.get("yes_bid_dollars"))
        if ya is None or yb is None or ya <= 0 or yb <= 0:
            continue
        no_ask = 1 - yb
        side, cost = ("yes", ya) if ya >= no_ask else ("no", no_ask)
        min_px, min_z = _min_price(ticker), _min_sigma(ticker)
        # 27 Oct 14:33 CT (Matthew): near-miss line for the yield markets -- price, sigma and what blocked the bid,
        # so we can see how close they come.
        near = _is_rate(ticker) and cost >= MK_NEAR_MISS_FLOOR
        zs_in = _held_sigma(ticker, m, side, sl)
        why = []
        max_spread = MK_FX_MAX_SPREAD if _is_fx(ticker) else MK_MAX_SPREAD
        if ya - yb > max_spread + 1e-9:
            why.append(f"spread {(ya - yb) * 100:.0f}c > {max_spread * 100:.0f}c")
        if _is_fx(ticker) and sl > MK_FX_MAX_SECONDS:
            why.append(f"{sl:.0f}s left > {MK_FX_MAX_SECONDS}s")
        if not (min_px <= cost <= MK_MAX_PRICE):
            why.append(f"price {cost * 100:.0f}c outside {min_px * 100:.0f}-{MK_MAX_PRICE * 100:.0f}c")
        if zs_in is None:
            why.append("no sigma value")
        elif zs_in < min_z - 1e-9:
            why.append(f"sigma {zs_in:.2f} < {min_z}")
        if why:
            if near:
                _note("near:" + ticker, f"[MKTS-NEAR] {ticker} ({_label(ticker)}): {side.upper()} {cost * 100:.0f}c, "
                      f"To flip {'?' if zs_in is None else f'{zs_in:.2f}'} sigma, {sl:.0f}s left -- "
                      f"not bid: {'; '.join(why)}", every=MK_NEAR_MISS_EVERY)
            elif zs_in is None and min_px <= cost <= MK_MAX_PRICE:
                # 26 Oct 19:41 CT (Matthew): no sigma value -> no bid (hourly markets, no Yahoo match, bad price match).
                _note(ticker, f"[MKTS] {ticker}: no sigma value for it -- not bidding.", every=300.0)
            # 26 Oct 20:10 CT: sigma bar as crypto Pulse. Entries at 0.62 / 0.66 / -0.58 sigma (Brent, USDCAD,
            # EURUSD) were sold within seconds for -$1.30; the one at 2.14 sigma won.
            continue
        count = MK_FX_SHARES if _is_fx(ticker) else (MK_COMMOD_SHARES if _is_commodity(ticker) else MK_SHARES)
        if MK_PAPER:
            fee_in = _fee(MK_SHARES, cost)
            if _s["balance"] < cost * MK_SHARES + fee_in:
                continue
            _s["balance"] -= cost * MK_SHARES + fee_in
        else:
            _tried.add(ticker)
            _buying.add(ticker)
            count, filled_px = _buy(client, ticker, side, cost, count)
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
        print(f"{_tag()} ENTRY {ticker} ({_label(ticker)}): {side.upper()} {count:g} @ ${cost:.2f}, To flip {zs_in:.2f} sigma"
              f"{' (simulated)' if MK_PAPER else ''}, {sl:.0f}s left")
        _save()
