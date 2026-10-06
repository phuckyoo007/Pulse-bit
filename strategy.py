"""
Discovers Kalshi's crypto up/down markets (KXBTC15M and equivalents),
figures out the strike + time remaining from each market's own fields,
and computes edge against the volatility model in vol_model.py.
Series tickers are discovered dynamically via get_series() rather than
hardcoded — Kalshi has renamed/rotated crypto series before, and
hardcoding a guess is exactly the mistake that caused real bugs earlier
in this project's sports version. Verify the printed series list the
first time you run this against your real account.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from price_feed import get_spot_and_vol
from vol_model import probability_above_strike, probability_below_strike
CRYPTO_SERIES_PREFIXES = {
    "KXBTC15M": "BTC",
    "KXSOL15M": "SOL",
    "KXXRP15M": "XRP",
    "KXBNB15M": "BNB",
    "KXDOGE15M": "DOGE",
    "KXBCH15M": "BCH",
    # KXETH15M: independently confirmed via a separate real, working
    # Kalshi bot's published config -- good confidence.
    "KXETH15M": "ETH",
    # KXHYPE15M: pattern-matched guess (every other coin follows
    # KX{COIN}15M exactly, and HYPE's 15-min market is confirmed to
    # exist), but NOT independently confirmed the way ETH now is.
    # Watch the console's market discovery output once this deploys.
    "KXHYPE15M": "HYPE",
}
HOURLY_CRYPTO_SERIES_PREFIXES = {}
COMMODITY_SERIES_PREFIXES = {
    "KXGOLD15M": "GOLD",
    "KXSILVER15M": "SILVER",
    "KXWTI15M": "OIL",
    # Unconfirmed pattern guesses -- harmless if wrong (logged, skipped).
    "KXCOPPER15M": "COPPER",
    "KXTIN15M": "TIN",
    "KXPLAT15M": "PLATINUM",
    "KXPLATINUM15M": "PLATINUM",
    "KXPALLADIUM15M": "PALLADIUM",
    "KXPALL15M": "PALLADIUM",
}
# Foreign-exchange 15-minute markets (EUR/USD, GBP/USD, USD/JPY). These
# three prefixes are PATTERN GUESSES (KX{PAIR}15M) -- not independently
# confirmed. discover_dynamic_series() below also scans Kalshi's real
# series list for any "...15M" series whose title mentions these pairs,
# so a wrong guess here gets corrected at runtime and is logged.
FX_SERIES_PREFIXES = {
    "KXEURUSD15M": "EURUSD",
    "KXGBPUSD15M": "GBPUSD",
    "KXUSDJPY15M": "USDJPY",
}
# Extra commodities that exist as 15-min markets but whose tickers are
# not confirmed -- only picked up if the live series list shows them.
_DYNAMIC_KEYWORDS = {
    "FX": {"EUR/USD": "EURUSD", "EURUSD": "EURUSD", "GBP/USD": "GBPUSD", "GBPUSD": "GBPUSD",
           "USD/JPY": "USDJPY", "USDJPY": "USDJPY"},
    "COMMODITY": {"NATURAL GAS": "NATGAS", "NATGAS": "NATGAS", "COPPER": "COPPER",
                  "TIN": "TIN", "PLATINUM": "PLATINUM", "PALLADIUM": "PALLADIUM",
                  "GOLD": "GOLD", "SILVER": "SILVER", "CRUDE": "OIL", "WTI": "OIL"},
}
INDEX_SERIES_PREFIXES = {
    "KXNDQ15M": "NASDAQ100",
    "KXINX15M": "SP500",
}
_vol_cache = {}
def get_cached_spot_and_vol(coin: str, max_age_seconds: int = 30):
    import time
    cached = _vol_cache.get(coin)
    if cached and (time.time() - cached[2]) < max_age_seconds:
        return cached[0], cached[1]
    spot, vol = get_spot_and_vol(coin)
    _vol_cache[coin] = (spot, vol, time.time())
    return spot, vol
def discover_crypto_markets(client) -> list:
    markets = []
    all_prefixes = {**CRYPTO_SERIES_PREFIXES, **HOURLY_CRYPTO_SERIES_PREFIXES}
    for prefix, coin in all_prefixes.items():
        try:
            resp = client.get_markets(status="open", series_ticker=prefix, limit=50)
            found = resp.get("markets", [])
            # FIXED, per real evidence -- this used to swallow errors
            # SILENTLY (except Exception: continue), which hid whether
            # a missing coin was a genuine API error (e.g. a wrong
            # ticker prefix) or just a real, temporary empty result
            # (e.g. between windows) -- those are very different
            # problems and this couldn't tell them apart before.
            if not found:
                print(f"  [discovery] {coin} ({prefix}): 0 open markets returned this cycle.")
            markets.extend(found)
        except Exception as e:
            print(f"  [discovery] {coin} ({prefix}): API ERROR -- {e}")
            continue
    return markets
def _extract_strike(market: dict) -> Optional[float]:
    for key in ("floor_strike", "cap_strike"):
        value = market.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    match = re.search(r"Target Price:\s*\$?([\d,]+(?:\.\d+)?)", market.get("yes_sub_title", "") or "")
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            pass
    custom = market.get("custom_strike") or {}
    for key in ("price", "strike", "threshold"):
        if key in custom:
            try:
                return float(custom[key])
            except (TypeError, ValueError):
                pass
    return None
def _extract_direction(market: dict) -> str:
    strike_type = market.get("strike_type", "")
    if strike_type in ("greater_or_equal", "greater"):
        return "above"
    if strike_type in ("less_or_equal", "less_than", "less"):
        return "below"
    title = (market.get("title", "") or "").lower()
    if "up" in title:
        return "above"
    if "down" in title:
        return "below"
    return "above"
def _time_to_expiry_years(market: dict) -> Optional[float]:
    close_time_str = market.get("close_time")
    if not close_time_str:
        return None
    close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    seconds_remaining = (close_time - now).total_seconds()
    if seconds_remaining <= 0:
        return None
    return seconds_remaining / (365.25 * 24 * 3600)
_dynamic_cache = [0.0]
_log_times = {}


def _log_throttled(key: str, msg: str, every_seconds: float = 600.0):
    import time
    now = time.time()
    if now - _log_times.get(key, 0.0) >= every_seconds:
        _log_times[key] = now
        print(msg)


def discover_dynamic_series(client, refresh_seconds: float = 900.0):
    """Scan Kalshi's real series list for 15-minute FX / commodity series
    not already configured, and add them at runtime. Never raises."""
    import time
    if time.time() - _dynamic_cache[0] < refresh_seconds:
        return
    _dynamic_cache[0] = time.time()
    try:
        resp = client.get_series(limit=1000)
    except Exception as e:
        _log_throttled("dyn_fail", f"Dynamic FX/commodity series discovery failed (non-fatal): {e}")
        return
    known = set(CRYPTO_SERIES_PREFIXES) | set(INDEX_SERIES_PREFIXES) | set(FX_SERIES_PREFIXES) | set(COMMODITY_SERIES_PREFIXES)
    for srs in resp.get("series", []):
        ticker = srs.get("ticker", "")
        if not ticker.endswith("15M") or ticker in known:
            continue
        text = f"{ticker} {srs.get('title', '')}".upper()
        for kind, table in (("FX", FX_SERIES_PREFIXES), ("COMMODITY", COMMODITY_SERIES_PREFIXES)):
            label = next((v for k, v in _DYNAMIC_KEYWORDS[kind].items()
                          if re.search(r'(?<![A-Z])' + re.escape(k) + r'(?![A-Z])', text)), None)
            if label:
                table[ticker] = label
                print(f"Dynamic discovery: added {kind} series {ticker} ({label}) from Kalshi's live series list.")
                break


def _discover_prefix_table(client, table: dict, kind: str) -> list:
    discover_dynamic_series(client)
    markets = []
    for prefix in list(table):
        try:
            resp = client.get_markets(status="open", series_ticker=prefix, limit=50)
            found = resp.get("markets", [])
            if not found:
                _log_throttled(f"empty_{prefix}", f"  [{kind}] {prefix}: 0 open markets (closed right now, or wrong ticker).")
            else:
                _log_throttled(f"ok_{prefix}", f"  [{kind}] {prefix}: {len(found)} open market(s) found.", 300.0)
            markets.extend(found)
        except Exception as e:
            _log_throttled(f"err_{prefix}", f"  [{kind}] {prefix}: API ERROR {e} -- skipping this series.")
    return markets


def discover_commodity_markets(client) -> list:
    return _discover_prefix_table(client, COMMODITY_SERIES_PREFIXES, "COMMODITY")


def discover_fx_markets(client) -> list:
    return _discover_prefix_table(client, FX_SERIES_PREFIXES, "FX")
@dataclass
class CommodityPriceResult:
    ticker: str
    title: str
    commodity: str
    market_price: float
    seconds_remaining: float
    volume: float
    close_time: Optional[datetime] = None
    yes_ask: float = 0.0
    yes_bid: float = 0.0
def evaluate_commodity_market(market: dict) -> Optional[CommodityPriceResult]:
    ticker = market.get("ticker", "")
    commodity = next((c for prefix, c in {**COMMODITY_SERIES_PREFIXES, **FX_SERIES_PREFIXES}.items()
                      if ticker.startswith(prefix)), None)
    if not commodity:
        return None
    close_time_str = market.get("close_time")
    close_time = None
    seconds_remaining = 0.0
    if close_time_str:
        close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        seconds_remaining = (close_time - datetime.now(timezone.utc)).total_seconds()
    yes_ask = market.get("yes_ask_dollars")
    yes_bid = market.get("yes_bid_dollars")
    if yes_ask is None or yes_bid is None:
        return None
    market_price = (float(yes_ask) + float(yes_bid)) / 2
    return CommodityPriceResult(
        ticker=ticker, title=market.get("title", ticker), commodity=commodity,
        market_price=market_price, seconds_remaining=seconds_remaining,
        volume=float(market.get("volume_fp", 0)), close_time=close_time,
        yes_ask=float(yes_ask), yes_bid=float(yes_bid),
    )
def discover_index_markets(client) -> list:
    markets = []
    for prefix in INDEX_SERIES_PREFIXES:
        try:
            resp = client.get_markets(status="open", series_ticker=prefix, limit=50)
            markets.extend(resp.get("markets", []))
        except Exception:
            continue
    return markets
@dataclass
class IndexPriceResult:
    ticker: str
    title: str
    index: str
    market_price: float
    seconds_remaining: float
    volume: float
    close_time: Optional[datetime] = None
    yes_ask: float = 0.0
    yes_bid: float = 0.0
def evaluate_index_market(market: dict) -> Optional[IndexPriceResult]:
    ticker = market.get("ticker", "")
    index = next((i for prefix, i in INDEX_SERIES_PREFIXES.items() if ticker.startswith(prefix)), None)
    if not index:
        return None
    close_time_str = market.get("close_time")
    close_time = None
    seconds_remaining = 0.0
    if close_time_str:
        close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        seconds_remaining = (close_time - datetime.now(timezone.utc)).total_seconds()
    yes_ask = market.get("yes_ask_dollars")
    yes_bid = market.get("yes_bid_dollars")
    if yes_ask is None or yes_bid is None:
        return None
    market_price = (float(yes_ask) + float(yes_bid)) / 2
    return IndexPriceResult(
        ticker=ticker, title=market.get("title", ticker), index=index,
        market_price=market_price, seconds_remaining=seconds_remaining,
        volume=float(market.get("volume_fp", 0)), close_time=close_time,
        yes_ask=float(yes_ask), yes_bid=float(yes_bid),
    )
# FIXED, per explicit request to open Pulse to ALL markets where the
# outcome is under 4 hours away. The ORIGINAL version of this function
# tried to classify a whole SERIES as "15min" or "hourly" by guessing
# from its title text (e.g. checking for "15 min" or "hourly" in the
# series title) -- that's exactly what broke: a daily multi-strike
# ladder market ("SOL price on Jul 30, 2026?") slipped through that
# heuristic and got treated as hourly, applying the wrong time
# restriction to the wrong kind of market entirely.
# This version does NOT try to classify the series at all -- it just
# returns every series Kalshi has that isn't already covered by crypto/
# commodity/index above. The actual "under 4 hours" decision happens
# later, per INDIVIDUAL MARKET, using that market's own real close_time
# field (see bot.py) -- never a guess based on series-level text.
GENERIC_MAX_SECONDS_REMAINING = 600  # TIGHTENED further from 800s to 600s (10 min), per explicit request
def discover_generic_short_markets(client) -> dict:
    known_prefixes = (set(CRYPTO_SERIES_PREFIXES) | set(HOURLY_CRYPTO_SERIES_PREFIXES)
                      | set(COMMODITY_SERIES_PREFIXES) | set(INDEX_SERIES_PREFIXES)
                      | set(FX_SERIES_PREFIXES))
    found = {}
    try:
        resp = client.get_series(limit=200)
    except Exception as e:
        print(f"Generic series discovery failed: {e}")
        return found
    for s in resp.get("series", []):
        ticker = s.get("ticker", "")
        if not ticker or ticker in known_prefixes:
            continue
        category = s.get("category", "unknown")
        # No "15min"/"hourly" classification here on purpose -- see
        # docstring above. Every non-crypto/commodity/index series gets
        # returned; bot.py filters by each INDIVIDUAL market's real
        # close_time, not by a guess about the series as a whole.
        found[ticker] = category
    return found
@dataclass
class GenericPriceResult:
    """Pure price-based result for any non-crypto market -- deliberately
    has NO model_prob at all, matching the pure-price entry logic
    already used for commodities and indices."""
    ticker: str
    title: str
    category: str
    market_price: float
    seconds_remaining: float
    volume: float
    close_time: Optional[datetime] = None
    yes_ask: float = 0.0
    yes_bid: float = 0.0
def evaluate_generic_market(market: dict, category: str) -> Optional[GenericPriceResult]:
    close_time_str = market.get("close_time")
    close_time = None
    seconds_remaining = 0.0
    if close_time_str:
        close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        seconds_remaining = (close_time - datetime.now(timezone.utc)).total_seconds()
    yes_ask = market.get("yes_ask_dollars")
    yes_bid = market.get("yes_bid_dollars")
    if yes_ask is None or yes_bid is None:
        return None
    market_price = (float(yes_ask) + float(yes_bid)) / 2
    return GenericPriceResult(
        ticker=market.get("ticker", ""), title=market.get("title", market.get("ticker", "")),
        category=category, market_price=market_price, seconds_remaining=seconds_remaining,
        volume=float(market.get("volume_fp", 0)), close_time=close_time,
        yes_ask=float(yes_ask), yes_bid=float(yes_bid),
    )
@dataclass
class CryptoEdgeResult:
    ticker: str
    title: str
    coin: str
    direction: str
    market_price: float
    model_prob: float
    edge_pct: float
    seconds_remaining: float
    volume: float
    close_time: Optional[datetime] = None
    is_hourly: bool = False
    yes_ask: float = 0.0
    yes_bid: float = 0.0
def evaluate_crypto_market(market: dict) -> Optional[CryptoEdgeResult]:
    ticker = market["ticker"]
    coin = next((c for prefix, c in CRYPTO_SERIES_PREFIXES.items() if ticker.startswith(prefix)), None)
    is_hourly = False
    if not coin:
        coin = next((c for prefix, c in HOURLY_CRYPTO_SERIES_PREFIXES.items() if ticker.startswith(prefix)), None)
        is_hourly = coin is not None
    if not coin:
        return None
    strike = _extract_strike(market)
    time_to_expiry = _time_to_expiry_years(market)
    if strike is None or time_to_expiry is None:
        return None
    spot, vol = get_cached_spot_and_vol(coin)
    if spot is None or vol is None:
        return None
    direction = _extract_direction(market)
    if direction == "above":
        model_prob = probability_above_strike(spot, strike, time_to_expiry, vol)
    else:
        model_prob = probability_below_strike(spot, strike, time_to_expiry, vol)
    if model_prob is None:
        return None
    yes_ask = market.get("yes_ask_dollars")
    yes_bid = market.get("yes_bid_dollars")
    if yes_ask is None or yes_bid is None:
        return None
    market_price = (float(yes_ask) + float(yes_bid)) / 2
    edge_pct = (model_prob - market_price) * 100
    return CryptoEdgeResult(
        ticker=ticker, title=market.get("title", ticker), coin=coin, direction=direction,
        market_price=market_price, model_prob=model_prob, edge_pct=edge_pct,
        seconds_remaining=time_to_expiry * 365.25 * 24 * 3600,
        volume=float(market.get("volume_fp", 0)),
        close_time=datetime.fromisoformat(market["close_time"].replace("Z", "+00:00")) if market.get("close_time") else None,
        is_hourly=is_hourly, yes_ask=float(yes_ask), yes_bid=float(yes_bid),
    )