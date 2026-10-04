"""
Squeeze Momentum indicator (John Carter's TTM Squeeze, popularized on
TradingView by LazyBear), per explicit request -- a TRIAL, purely
observational, same "log it, don't bet on it yet" philosophy as
reversion.py's dip-recovery signal. Does NOT place any order, real or
dry-run -- only returns a signal so bot.py can print/log it, so real
data can accumulate on how often it fires and whether it would have
called the right direction, before ever risking money on it.

WHAT IT DOES: combines Bollinger Bands and Keltner Channels to detect
when a market has gone quiet (bands compress tightly inside the
channels -- low volatility, a "squeeze"), then flags the moment that
squeeze releases (bands expand back outside the channels) along with
which direction the momentum favors.

HONEST ADAPTATION FROM THE STANDARD VERSION: the real indicator's
Keltner Channel width normally uses ATR (Average True Range), which
needs each candle's high/low, not just its close. price_feed.py only
ever returns closing prices (that's all Coinbase/CoinGecko give this
project at this granularity) -- there's no intraday high/low to build
a true ATR from. This uses the average ABSOLUTE close-to-close change
over the same window as a stand-in for ATR instead. It's a real,
reasonable proxy for short-term volatility, but it is not identical to
the textbook indicator -- flagged here plainly rather than silently
treating this as the "real" TTM Squeeze.
"""
import math
from dataclasses import dataclass
from typing import Optional

# Standard default settings, per how this indicator is normally run
# (confirmed against the TTM Squeeze / LazyBear TradingView version).
BB_LENGTH = 20
BB_STDEV_MULT = 2.0
KC_LENGTH = 20
KC_ATR_MULT = 1.5

_squeeze_state = {}   # ticker -> "on" or "off" (whether currently squeezed), so a release can be detected as a transition


@dataclass
class SqueezeMomentumSignal:
    ticker: str
    direction: str      # "bullish" or "bearish"
    momentum: float     # the momentum histogram value at the moment of release -- larger magnitude = stronger conviction


def _sma(values: list) -> float:
    return sum(values) / len(values)


def _stdev(values: list, mean: float) -> float:
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _avg_abs_change(values: list) -> float:
    """Stand-in for ATR -- see the module docstring's honest caveat
    about why this isn't the textbook ATR calculation."""
    changes = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    return sum(changes) / len(changes) if changes else 0.0


def check_squeeze_momentum(ticker: str, prices: list) -> Optional[SqueezeMomentumSignal]:
    """Call with a real, ordered (oldest-first) closing-price series for
    a coin -- the same list price_feed.py's fetch functions already
    return. Needs at least BB_LENGTH/KC_LENGTH prices to compute
    anything at all; returns None until enough history is available.

    Returns a signal only at the exact moment a squeeze releases (was
    squeezed on the previous call, is no longer squeezed now) -- not on
    every call while squeezed, and not after the release has already
    been reported once, matching how this indicator is meant to be read
    (the release itself is the actionable moment, not the ongoing state)."""
    if len(prices) < max(BB_LENGTH, KC_LENGTH) + 1:
        return None

    window = prices[-BB_LENGTH:]
    mean = _sma(window)
    stdev = _stdev(window, mean)
    bb_upper = mean + BB_STDEV_MULT * stdev
    bb_lower = mean - BB_STDEV_MULT * stdev

    kc_window = prices[-KC_LENGTH:]
    kc_mean = _sma(kc_window)
    atr_proxy = _avg_abs_change(kc_window)
    kc_upper = kc_mean + KC_ATR_MULT * atr_proxy
    kc_lower = kc_mean - KC_ATR_MULT * atr_proxy

    is_squeezed = (bb_upper < kc_upper) and (bb_lower > kc_lower)
    was_squeezed = _squeeze_state.get(ticker) == "on"
    _squeeze_state[ticker] = "on" if is_squeezed else "off"

    if not (was_squeezed and not is_squeezed):
        return None   # no release happened on this call -- either still squeezed, or wasn't squeezed before either

    # Momentum direction, per the standard indicator: how far the
    # current price sits from its own recent midpoint -- positive means
    # price is currently above where it's been trending (bullish
    # release), negative means below (bearish release).
    momentum = prices[-1] - mean
    direction = "bullish" if momentum > 0 else "bearish"
    return SqueezeMomentumSignal(ticker=ticker, direction=direction, momentum=round(momentum, 4))


def cleanup_stale_tickers(active_tickers: set):
    """Call periodically, same pattern as reversion.py -- stop tracking
    tickers no longer being scanned."""
    for ticker in list(_squeeze_state.keys()):
        if ticker not in active_tickers:
            del _squeeze_state[ticker]
