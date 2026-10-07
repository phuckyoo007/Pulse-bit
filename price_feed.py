"""
Crypto price data for the volatility model, routed by coin symbol rather
than assuming every coin lives on one exchange:

  - BTC, ETH, SOL, XRP: Coinbase's public Exchange API (no auth), 1-minute
    candle granularity -- the more precise source, used where available.
  - BNB: Coinbase doesn't list it (it's Binance's own token -- the same
    gap noted earlier in this project when the Coinbase trading bot swapped
    BNB for ADA). Falls back to CoinGecko's public keyless API instead,
    which lists virtually any coin but only returns ~5-minute granularity
    for a lookback under a day -- coarser, so BNB's volatility estimate is
    inherently less precise than the Coinbase-sourced coins. Worth knowing
    if you're relying on it heavily.

This is used to compute realized volatility, standing in for the
"implied volatility" a real options chain would give you if one existed
at a 15-minute horizon (it doesn't, which is exactly why this needs its
own model rather than just checking an options market).
"""
import math
import time
from typing import Optional

import requests

COINBASE_EXCHANGE_BASE = "https://api.exchange.coinbase.com"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# How much recent history to use for the volatility estimate. Shorter
# windows react faster to current conditions but are noisier; longer
# windows are more stable but can miss a genuine recent vol spike (e.g.
# a news event) that should widen your probability estimate.
VOL_LOOKBACK_MINUTES = 60

# CoinGecko's free tier only returns ~5-minute granularity, not 1-minute
# like Coinbase -- the same 60-minute lookback gives Coinbase ~60 data
# points but CoinGecko only ~12, nowhere near enough for a stable stdev
# estimate. A noisy stdev from that few points can land spuriously near
# zero by chance, and -- confirmed by a real BNB market's data -- a
# near-zero volatility input makes the pricing model snap to 0%/100% for
# almost any price gap, however small. Use a longer window specifically
# for CoinGecko-sourced coins to get enough samples for a stable read.
COINGECKO_VOL_LOOKBACK_MINUTES = 240   # ~48 data points at ~5-min spacing

# Defensive floor: no major crypto asset realistically trades with
# annualized volatility below this even in quiet periods. An estimate
# below it is far more likely a data/sampling artifact than genuine calm
# -- flooring it prevents that artifact from creating a falsely extreme
# probability, the same way a wrong strike price used to.
MIN_ANNUALIZED_VOL = 0.20

COINBASE_PRODUCT_MAP = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "ADA": "ADA-USD",
    "DOGE": "DOGE-USD",
    "BCH": "BCH-USD",
    "ZEC": "ZEC-USD",
    "HYPE": "HYPE-USD",   # confirmed listed on Coinbase Feb 2025
    "NEAR": "NEAR-USD",   # confirmed working, but only ~28 data points/hour (lower trading volume than BTC/ETH-tier)
    "SUI": "SUI-USD",     # added for Kalshi's SUI 15-min market (Coinbase lists SUI-USD)
    "TON": "TON-USD",     # confirmed working, but only ~18 data points/hour -- the sparsest of the 12 coins
}
COINGECKO_ID_MAP = {
    "BNB": "binancecoin",
}


def _fetch_coinbase_prices(product_id: str, minutes: int) -> list:
    """Returns closing prices, 1-minute granularity, oldest first."""
    end = int(time.time())
    start = end - minutes * 60
    resp = requests.get(
        f"{COINBASE_EXCHANGE_BASE}/products/{product_id}/candles",
        params={"start": start, "end": end, "granularity": 60},
        timeout=10,
    )
    resp.raise_for_status()
    candles = resp.json()   # each candle: [time, low, high, open, close, volume]
    candles.sort(key=lambda c: c[0])
    return [c[4] for c in candles]


def _fetch_coingecko_prices(coin_id: str, minutes: int) -> list:
    """Returns closing prices, ~5-minute granularity (CoinGecko's auto-tier
    for a sub-1-day range on the free keyless tier), oldest first.

    Retries with backoff specifically on a 429 (rate limit) -- this is a
    real, expected condition on CoinGecko's free tier, not a genuine
    failure, and is made meaningfully more likely here since running two
    bot processes in the same folder (each with its own independent
    cache) roughly doubles the real call rate against the same
    rate-limited endpoint from the same IP."""
    end = int(time.time())
    start = end - minutes * 60
    max_retries = 2
    backoff_seconds = 2
    for attempt in range(max_retries + 1):
        resp = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}/market_chart/range",
            params={"vs_currency": "usd", "from": start, "to": end},
            timeout=10,
        )
        if resp.status_code == 429:
            if attempt < max_retries:
                wait = backoff_seconds * (attempt + 1)
                print(f"CoinGecko rate-limited (429) -- waiting {wait}s before retry {attempt + 1}/{max_retries}.")
                time.sleep(wait)
                continue
            else:
                resp.raise_for_status()   # out of retries -- raise the real error, same as before
        resp.raise_for_status()
        data = resp.json()
        prices = data.get("prices", [])   # each: [timestamp_ms, price]
        prices.sort(key=lambda p: p[0])
        return [p[1] for p in prices]
    return []   # unreachable in practice, keeps type checkers happy


def realized_volatility(prices: list, sample_interval_seconds: float) -> Optional[float]:
    """
    Annualized realized volatility from a list of prices, using
    log-returns. `sample_interval_seconds` must match the actual spacing
    of the data (60 for Coinbase's 1-min candles, ~300 for CoinGecko's
    ~5-min tier) -- annualizing with the wrong interval silently produces
    a wrong volatility estimate, so this is a required argument on
    purpose rather than a hardcoded assumption.
    """
    if len(prices) < 10:
        return None
    log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
    if len(log_returns) < 5:
        return None
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    interval_vol = math.sqrt(variance)
    periods_per_year = (365.25 * 24 * 3600) / sample_interval_seconds
    return interval_vol * math.sqrt(periods_per_year)


_recent_prices_cache = {}   # coin -> (prices, fetched_at) -- separate cache from get_spot_and_vol's, so this doesn't double the real call rate


def get_recent_prices(coin: str, max_age_seconds: int = 30) -> Optional[list]:
    """Returns the raw, ordered (oldest-first) closing-price list for a
    coin, routed through the same Coinbase/CoinGecko sources as
    get_spot_and_vol() -- for callers (like squeeze_momentum.py) that
    need the actual series, not just the final spot/vol numbers.
    Cached separately from get_spot_and_vol()'s own cache, on purpose --
    sharing one cache between two different callers with different
    lookback needs would risk one silently getting the other's stale
    window length."""
    cached = _recent_prices_cache.get(coin)
    if cached and (time.time() - cached[1]) < max_age_seconds:
        return cached[0]
    try:
        if coin in COINBASE_PRODUCT_MAP:
            prices = _fetch_coinbase_prices(COINBASE_PRODUCT_MAP[coin], VOL_LOOKBACK_MINUTES)
        elif coin in COINGECKO_ID_MAP:
            prices = _fetch_coingecko_prices(COINGECKO_ID_MAP[coin], COINGECKO_VOL_LOOKBACK_MINUTES)
        else:
            return None
        _recent_prices_cache[coin] = (prices, time.time())
        return prices
    except Exception as e:
        print(f"Price feed error fetching recent prices for {coin}: {e}")
        return None


def get_spot_and_vol(coin: str) -> tuple:
    """Returns (spot_price, annualized_vol) for a coin symbol (e.g. "BTC",
    "BNB"), routed to the right source automatically. Either value may be
    None if the fetch or the vol calc didn't have enough data."""
    try:
        if coin in COINBASE_PRODUCT_MAP:
            prices = _fetch_coinbase_prices(COINBASE_PRODUCT_MAP[coin], VOL_LOOKBACK_MINUTES)
            interval_seconds = 60
        elif coin in COINGECKO_ID_MAP:
            prices = _fetch_coingecko_prices(COINGECKO_ID_MAP[coin], COINGECKO_VOL_LOOKBACK_MINUTES)
            interval_seconds = 300
        else:
            print(f"No price source configured for {coin}")
            return None, None

        if not prices:
            return None, None
        spot = prices[-1]
        vol = realized_volatility(prices, interval_seconds)
        if vol is not None and vol < MIN_ANNUALIZED_VOL:
            print(f"{coin}: realized vol estimate ({vol:.3f}) below sanity floor -- using floor of {MIN_ANNUALIZED_VOL} instead")
            vol = MIN_ANNUALIZED_VOL
        return spot, vol
    except Exception as e:
        print(f"Price feed error for {coin}: {e}")
        return None, None


# ---------------------------------------------------------------------------
# Commodities / FX via Pyth Network's Hermes API (keyless). Kalshi settles its gold/FX/commodity
# 15-minute markets on Pyth feeds, so Pyth is the right reference price (any other source would sit on a
# different basis than the strike). Pyth's old keyless history endpoint was shut down, so the bot builds its
# OWN price history by sampling Hermes' latest price every few seconds; volatility is estimated from that
# (it needs ~3 minutes of samples after each restart before it will produce a model).
# Everything here FAILS CLOSED: any problem returns (None, None) and the caller skips the trade.
# ---------------------------------------------------------------------------
from collections import deque

PYTH_HERMES_BASE = "https://pyth.dourolabs.app/hermes"   # upgraded Hermes (the old hermes.pyth.network host now rejects our key)
import os as _os


def _pyth_headers() -> dict:
    """Hermes now needs an API key (Pyth Terminal, free signup). Set it as the PYTH_API_KEY variable on Railway.
    Sent as a Bearer token. Never printed."""
    key = _os.environ.get("PYTH_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def pyth_key_configured() -> bool:
    return bool(_os.environ.get("PYTH_API_KEY", "").strip())
PYTH_SAMPLE_SECONDS = 4              # sample spacing for the self-built history
PYTH_HISTORY_SECONDS = 3600          # keep up to an hour of samples
PYTH_MIN_SAMPLES = 36                # ~3 minutes of 5s samples before a vol estimate is trusted
PYTH_MAX_STALENESS_SECONDS = 120     # newest Pyth publish must be this fresh, else market closed/stale -> no model
PYTH_MAX_REFERENCE_DEVIATION = 0.05  # chosen feed must sit within 5% of the Kalshi strike, else wrong feed

PYTH_SYMBOLS = {
    "GOLD": ["Metal.Index.GOLD/USD", "Metal.XAU/USD"],
    "SILVER": ["Metal.Index.SILVER/USD", "Metal.XAG/USD"],
    "PLATINUM": ["Metal.XPT/USD"],
    "PALLADIUM": ["Metal.XPD/USD"],
    "EURUSD": ["FX.EUR/USD"],
    "GBPUSD": ["FX.GBP/USD"],
    "USDJPY": ["FX.USD/JPY"],
    "OIL": [], "NATGAS": [], "COPPER": [],
}
PYTH_SEARCH_TERMS = {
    "GOLD": ["XAU", "gold"], "SILVER": ["XAG", "silver"], "PLATINUM": ["XPT", "platinum"], "PALLADIUM": ["XPD", "palladium"],
    "EURUSD": ["EUR/USD"], "GBPUSD": ["GBP/USD"], "USDJPY": ["USD/JPY"],
    "OIL": ["WTI", "USOIL", "crude"], "NATGAS": ["natural gas", "NATGAS", "NGAS"], "COPPER": ["copper", "XCU"],
}
# Annualized-vol sanity floors (a near-zero sample estimate makes the model snap to 0%/100%).
PYTH_VOL_FLOORS = {"EURUSD": 0.03, "GBPUSD": 0.03, "USDJPY": 0.03, "GOLD": 0.08, "SILVER": 0.12,
                   "PLATINUM": 0.12, "PALLADIUM": 0.15, "OIL": 0.15, "NATGAS": 0.25, "COPPER": 0.10}
PYTH_ASSETS = set(PYTH_SYMBOLS)

_pyth_feed_ids = {}        # symbol -> hermes feed id (hex, no 0x)
_pyth_chosen = {}          # asset -> (symbol, feed id) that passed validation
_pyth_search_cache = {}    # asset -> (symbols, fetched_at)
_pyth_samples = {}         # asset -> deque[(publish_time, price)]
_pyth_last_poll = {}       # asset -> time of last Hermes poll
_pyth_log_times = {}


def _pyth_log(key: str, msg: str, every: float = 300.0):
    now = time.time()
    if now - _pyth_log_times.get(key, 0.0) >= every:
        _pyth_log_times[key] = now
        print(msg)


def _pyth_search_symbols(asset: str) -> list:
    """Symbols (best guesses first) for an asset: hardcoded ones, then whatever Hermes' search returns."""
    cached = _pyth_search_cache.get(asset)
    if cached and time.time() - cached[1] < 3600:
        return cached[0]
    found = []
    for term in PYTH_SEARCH_TERMS.get(asset, []):
        try:
            resp = requests.get(f"{PYTH_HERMES_BASE}/v2/price_feeds", params={"query": term}, headers=_pyth_headers(), timeout=10)
            resp.raise_for_status()
            for feed in resp.json():
                sym = (feed.get("attributes") or {}).get("symbol")
                fid = feed.get("id")
                if not sym or not fid:
                    continue
                _pyth_feed_ids[sym] = fid.lower().replace("0x", "")
                if sym not in found and not sym.startswith(("Crypto.", "Equity.")):
                    found.append(sym)
        except Exception as e:
            _pyth_log(f"pyth_search_{asset}", f"Pyth symbol search for {asset} ('{term}') failed: {e}")
    # prefer Kalshi-style "Index" feeds, then everything else
    found.sort(key=lambda sy: (0 if ".Index." in sy and "/R" not in sy else 1))
    ordered = list(PYTH_SYMBOLS.get(asset, [])) + [sy for sy in found if sy not in PYTH_SYMBOLS.get(asset, [])]
    _pyth_search_cache[asset] = (ordered, time.time())
    if ordered:
        print(f"Pyth symbol search for {asset}: {ordered[:10]}")
    return ordered


def _pyth_latest(feed_ids: list) -> dict:
    """{feed_id: (price, publish_time)} for the given Hermes feed ids (one request)."""
    resp = requests.get(f"{PYTH_HERMES_BASE}/v2/updates/price/latest",
                        params=[("ids[]", i) for i in feed_ids] + [("parsed", "true")], headers=_pyth_headers(), timeout=10)
    resp.raise_for_status()
    out = {}
    for item in resp.json().get("parsed", []):
        pr = item.get("price") or {}
        try:
            out[item["id"].lower().replace("0x", "")] = (int(pr["price"]) * (10 ** int(pr["expo"])), int(pr["publish_time"]))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _pyth_choose_feed(asset: str, reference_price: Optional[float]):
    symbols = _pyth_search_symbols(asset)
    ids = {sy: _pyth_feed_ids[sy] for sy in symbols if sy in _pyth_feed_ids}
    if not ids:
        _pyth_log(f"pyth_noid_{asset}", f"Pyth {asset}: Hermes returned no feed ids for {symbols[:6]} -- no model, so no trade.")
        return None
    latest = _pyth_latest(list(ids.values()))
    for sym, fid in ids.items():
        if fid not in latest:
            continue
        price, pub = latest[fid]
        if reference_price and (price <= 0 or abs(math.log(price / reference_price)) > PYTH_MAX_REFERENCE_DEVIATION):
            _pyth_log(f"pyth_ref_{sym}", f"Pyth {asset}: {sym} price {price} is >5% from the Kalshi strike {reference_price} -- not the right feed, skipping it.")
            continue
        _pyth_chosen[asset] = (sym, fid)
        print(f"Pyth feed for {asset}: using {sym} (price {price}, last publish {int(time.time()) - pub}s ago)")
        return _pyth_chosen[asset]
    _pyth_log(f"pyth_none_{asset}", f"Pyth {asset}: none of {list(ids)[:8]} gave a usable price near the Kalshi strike -- no model, so no trade.")
    return None


def get_pyth_spot_and_vol(asset: str, reference_price: Optional[float] = None) -> tuple:
    """(spot, annualized_vol) for a commodity/FX asset from Pyth (the feed Kalshi settles on). Builds its own price
    history by sampling every PYTH_SAMPLE_SECONDS. Returns (None, None) until enough samples exist / when stale."""
    try:
        if asset not in PYTH_ASSETS:
            return None, None
        chosen = _pyth_chosen.get(asset) or _pyth_choose_feed(asset, reference_price)
        if not chosen:
            return None, None
        sym, fid = chosen
        buf = _pyth_samples.setdefault(asset, deque())
        now = time.time()
        if now - _pyth_last_poll.get(asset, 0.0) >= 1.0:
            _pyth_last_poll[asset] = now
            got = _pyth_latest([fid]).get(fid)
            if got:
                price, pub = got
                if now - pub > PYTH_MAX_STALENESS_SECONDS:
                    _pyth_log(f"pyth_stale_{asset}", f"Pyth {asset} ({sym}): last publish {int(now - pub)}s ago -- market closed/stale, no model.")
                    buf.clear()
                    return None, None
                if price > 0 and (not buf or pub - buf[-1][0] >= PYTH_SAMPLE_SECONDS):
                    buf.append((pub, price))
                while buf and buf[-1][0] - buf[0][0] > PYTH_HISTORY_SECONDS:
                    buf.popleft()
        if not buf or now - buf[-1][0] > PYTH_MAX_STALENESS_SECONDS:
            return None, None
        spot = buf[-1][1]
        if reference_price and abs(math.log(spot / reference_price)) > PYTH_MAX_REFERENCE_DEVIATION:
            _pyth_log(f"pyth_drift_{asset}", f"Pyth {asset} ({sym}): price {spot} drifted >5% from strike {reference_price} -- re-selecting feed.")
            _pyth_chosen.pop(asset, None); buf.clear()
            return None, None
        if len(buf) < PYTH_MIN_SAMPLES:
            _pyth_log(f"pyth_warm_{asset}", f"Pyth {asset}: warming up ({len(buf)}/{PYTH_MIN_SAMPLES} samples) -- no model yet.", 60.0)
            return None, None
        prices = [p for _, p in buf]
        span = buf[-1][0] - buf[0][0]
        vol = realized_volatility(prices, span / (len(prices) - 1))
        floor = PYTH_VOL_FLOORS.get(asset, 0.10)
        if vol is not None and vol < floor:
            vol = floor
        return spot, vol
    except Exception as e:
        _pyth_log(f"pyth_err_{asset}", f"Pyth price feed error for {asset}: {e}")
        return None, None
