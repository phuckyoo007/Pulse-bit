"""
Scores and ranks qualifying trade candidates by potential upside, per
explicit request: a bid at 88 cents has 12 cents of room to reach
$1.00 if it's right; a bid at 96 cents only has 4. Given a scan cycle
finds multiple qualifying candidates at once, this decides which ones
get acted on first -- the ones with more room to gain, not just
whichever coin happened to be checked first in the loop.

This does NOT change which trades qualify (that's still bot.py's price
range check) -- it only changes the ORDER they get entered in when more
than one qualifies in the same cycle.
"""
from dataclasses import dataclass


@dataclass
class RankedCandidate:
    ticker: str
    side: str          # "bid" or "ask"
    trade_price: float
    potential_points: float   # cents of room remaining to $1.00, out of 100
    original: object    # the underlying CryptoEdgeResult, passed through unchanged


def potential_points(trade_price: float) -> float:
    """Cents of room remaining to $1.00 if the trade is right, out of
    100 -- e.g. a trade at 88 cents has 12 points of room; one at 96
    cents has only 4."""
    return round((1.0 - trade_price) * 100, 2)


def size_scale_factor(trade_price: float, min_price: float, max_price: float,
                       floor: float = 0.3) -> float:
    """Scales position size proportionally to potential upside within
    the current price range, per explicit request -- a trade at the
    low end of the range (most room to gain) gets the full size; one
    at the high end (least room) gets scaled down toward `floor`, not
    all the way to zero -- a qualifying trade should still get a real,
    meaningful position, just a smaller one, not be silently excluded
    by scaling it out of existence. Returns a value in [floor, 1.0].
    Guards against min_price == max_price (would make the range
    meaningless) by returning 1.0 in that edge case rather than
    dividing by zero."""
    max_points = potential_points(min_price)   # the low end of the range has the MOST room
    min_points = potential_points(max_price)   # the high end has the LEAST room
    if max_points <= min_points:
        return 1.0
    this_points = potential_points(trade_price)
    fraction = max(0.0, min(1.0, (this_points - min_points) / (max_points - min_points)))
    return floor + fraction * (1.0 - floor)


def size_scale_factor_91_95(trade_price: float) -> float:
    """Per explicit request: full size (1.0) for any price at or below
    92 cents, linearly dropping to exactly 1/4 size by 95 cents, then
    flat at 1/4 for anything at or above 95 cents. Unlike
    size_scale_factor above, this is a fixed shape tied to specific
    price points (92c and 95c), not scaled relative to whatever the
    active entry range happens to be."""
    FULL_SIZE_UPPER_BOUND = 0.92
    QUARTER_SIZE_LOWER_BOUND = 0.95
    QUARTER_SIZE = 0.25

    if trade_price <= FULL_SIZE_UPPER_BOUND:
        return 1.0
    if trade_price >= QUARTER_SIZE_LOWER_BOUND:
        return QUARTER_SIZE
    fraction = (trade_price - FULL_SIZE_UPPER_BOUND) / (QUARTER_SIZE_LOWER_BOUND - FULL_SIZE_UPPER_BOUND)
    return 1.0 - fraction * (1.0 - QUARTER_SIZE)


def rank_candidates(candidates: list) -> list:
    """Takes a list of RankedCandidate and returns them sorted by
    potential_points descending -- the biggest remaining upside first.
    Ties (equal potential_points) keep their original relative order,
    since Python's sort is stable."""
    return sorted(candidates, key=lambda c: -c.potential_points)
