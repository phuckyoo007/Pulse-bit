"""
Prices a Kalshi crypto up/down contract the way a digital (binary) option
would be priced — using the Black-Scholes framework, with REALIZED
volatility standing in for implied volatility (no listed option expires
in 15 minutes, so there's no market-implied vol to borrow; this is the
standard workaround, not a shortcut).

This gives P(spot ends above strike at expiry) under a lognormal
random-walk assumption. That assumption is a simplification — real
crypto prices have fatter tails and can jump on news — which is exactly
why this should inform position sizing (via edge, through Kelly), not be
treated as certain.
"""
import math
from typing import Optional


def _norm_cdf(x: float) -> float:
    """Standard normal CDF, via the erf function (no scipy dependency needed)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def probability_above_strike(spot: float, strike: float, time_to_expiry_years: float, sigma: float) -> Optional[float]:
    """
    P(spot_price_at_expiry > strike), under geometric Brownian motion with
    zero drift (a reasonable assumption over a 15-minute window — real
    expected drift over 15 minutes is negligible compared to volatility).

    This is exactly the N(d2) term from Black-Scholes digital option
    pricing.
    """
    if spot is None or sigma is None or spot <= 0 or strike <= 0 or sigma <= 0 or time_to_expiry_years <= 0:
        return None
    d2 = (math.log(spot / strike) - 0.5 * sigma ** 2 * time_to_expiry_years) / (sigma * math.sqrt(time_to_expiry_years))
    return _norm_cdf(d2)


def probability_below_strike(spot: float, strike: float, time_to_expiry_years: float, sigma: float) -> Optional[float]:
    p_above = probability_above_strike(spot, strike, time_to_expiry_years, sigma)
    return None if p_above is None else 1 - p_above
