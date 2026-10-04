"""
A SEPARATE, UNPROVEN hypothesis from the main volatility model: tracks
each contract's own recent price history within its 15-minute life, and
flags when the current price has deviated unusually far from its own
short-term rolling average -- the "did it dip hard and might it revert"
signal.

IMPORTANT: this module only OBSERVES and LOGS. It does not place trades
or influence position sizing. That's deliberate. "Buy the dip because it
dipped" is a real, well-known failure mode (the gambler's fallacy applied
to a random walk) if there's nothing backing it beyond the dip itself --
a genuinely crashing asset punishes mechanical dip-buying badly. Before
this should ever touch a real trade, it needs the same treatment every
other real signal in this project got: log it, accumulate real
settlement outcomes, and run it through suggest_adjustments.py-style
significance testing to see if it's real or just noise that felt
compelling while watching a chart (which is a very common, very human,
very unreliable way to read price series -- hindsight makes every
random walk look like it had obvious turning points).
"""
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Optional

# How much recent price history (in ticks, roughly ~10s apart at the
# default poll interval) to use for the rolling average/stdev.
WINDOW_SIZE = 12   # roughly the last 2 minutes at a 10s poll interval

_price_history = defaultdict(lambda: deque(maxlen=WINDOW_SIZE))


@dataclass
class ReversionSignal:
    ticker: str
    current_price: float
    rolling_mean: float
    rolling_stdev: float
    z_score: Optional[float]   # how many stdevs the current price is from its own recent mean


def record_and_score(ticker: str, current_price: float) -> ReversionSignal:
    """Call this once per market per loop -- records the current price
    into that ticker's rolling history, then scores how far the current
    price sits from its own recent trend. A large negative z-score means
    the price has dipped well below where it's recently been trading; a
    large positive z-score means it's spiked well above."""
    history = _price_history[ticker]
    history.append(current_price)

    if len(history) < 4:   # not enough history yet for a meaningful stdev
        return ReversionSignal(ticker, current_price, current_price, 0.0, None)

    mean = sum(history) / len(history)
    variance = sum((p - mean) ** 2 for p in history) / (len(history) - 1)
    stdev = variance ** 0.5

    if stdev == 0:
        return ReversionSignal(ticker, current_price, mean, 0.0, None)

    z = (current_price - mean) / stdev
    return ReversionSignal(ticker, current_price, round(mean, 4), round(stdev, 4), round(z, 3))


def cleanup_stale_tickers(active_tickers: set):
    """Call periodically to stop tracking tickers that are no longer
    being scanned (their 15-minute window closed) -- keeps memory bounded
    instead of accumulating every ticker ever seen."""
    for ticker in list(_price_history.keys()):
        if ticker not in active_tickers:
            del _price_history[ticker]
    for ticker in list(_momentum_state.keys()):
        if ticker not in active_tickers:
            del _momentum_state[ticker]


# TRIAL, per explicit request -- purely observational, same "log it,
# don't bet on it yet" philosophy as the rest of this module. Detects
# whether a market's "no" side has genuinely RISEN through roughly 20%,
# then roughly 40%, before reaching 60% or higher, all within the final
# 60 seconds before close -- a real progression, not just "touched 60%
# at some point" (which could happen from a single jump with no actual
# momentum behind it at all). This does NOT place any order, real or
# dry-run -- it only returns a signal so bot.py can print/log it for
# later review, the same way this project treated every other new
# signal before trusting it with real money.
MOMENTUM_WINDOW_SECONDS = 60          # only tracked in the final 60 seconds before close
MOMENTUM_STAGE1_MAX_PRICE = 0.25      # "roughly 20%" -- must have been at or below this at some point
MOMENTUM_STAGE2_RANGE = (0.35, 0.45)  # "roughly 40%" -- must have passed through this range next
MOMENTUM_TRIGGER_PRICE = 0.60         # signal fires once "no" reaches this, but ONLY after stages 1 and 2 above were both seen first

_momentum_state = defaultdict(lambda: {"stage": 0, "fired": False})   # ticker -> {"stage": 0/1/2, "fired": bool}


@dataclass
class MomentumSignal:
    ticker: str
    no_price: float
    seconds_remaining: float


def check_momentum_turnaround(ticker: str, no_price: float, seconds_remaining: float) -> Optional[MomentumSignal]:
    """Call once per market per loop, alongside record_and_score().
    Returns a MomentumSignal the FIRST time the genuine 20%->40%->60%
    progression completes for this ticker this window, else None.
    Only ever tracks/fires within the final MOMENTUM_WINDOW_SECONDS --
    outside that window, this resets the ticker's progress entirely,
    since "genuine last-minute momentum" is specifically what's being
    looked for here, not a slow drift over the whole 15 minutes."""
    state = _momentum_state[ticker]

    if seconds_remaining > MOMENTUM_WINDOW_SECONDS:
        state["stage"] = 0
        state["fired"] = False
        return None

    if state["fired"]:
        return None   # already signaled once for this ticker this window -- don't repeat

    if state["stage"] == 0 and no_price <= MOMENTUM_STAGE1_MAX_PRICE:
        state["stage"] = 1
    elif state["stage"] == 1 and MOMENTUM_STAGE2_RANGE[0] <= no_price <= MOMENTUM_STAGE2_RANGE[1]:
        state["stage"] = 2
    elif state["stage"] == 2 and no_price >= MOMENTUM_TRIGGER_PRICE:
        state["fired"] = True
        return MomentumSignal(ticker=ticker, no_price=no_price, seconds_remaining=seconds_remaining)

    return None
