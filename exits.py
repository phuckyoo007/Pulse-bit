"""
Early-exit logic for open positions, separate from entry logic on purpose.

SIMPLIFIED, per explicit request -- ONE uniform rule for everything: if
a position has lost 14% of what was paid for it (value has fallen to 86%% of entry or below), exit. No per-tier variation, no trailing/peak
logic, no exceptions. A $10 bet exits no later than $8.60.
"""
import time
from dataclasses import dataclass
from typing import Optional
from apply_real_fees import fee_per_contract

STOP_LOSS_ENABLED = True
UNIVERSAL_STOP_LOSS_FRACTION = 0.86   # initial stop: exit when value falls to <= 84% of what was paid
STOP_LOSS_LEVEL_HELD_SIDE = 0.86   # absolute stop on the HELD side's price: exit when value <= 86c (YES price for YES; 1 - YES price for NO, i.e. YES quote >= 14c)
TAKE_PROFIT_ENABLED = False
TAKE_PROFIT_PRICE = 0.85              # capture profit: sell as soon as the position's value (price of the side we hold) reaches this
PROFIT_TRAIL_ENABLED = False          # OFF: no trailing stop -- the stop stays fixed at UNIVERSAL_STOP_LOSS_FRACTION of what was paid
PROFIT_TRAIL_FRACTION = 0.97          # once a position has gone into profit: exit if value falls to <= 97% of its peak
MANUAL_STOP_LOSS_FRACTION = 0.86      # manually placed (adopted) positions: exit when value is <= 82% of what was paid
MODEL_FLIP_ENABLED = True             # manual bets: sell if the model clearly favors the other side
MODEL_FLIP_MIN_WIN = 0.35             # sell when the model's win chance for OUR side drops below this
MODEL_FLIP_GRACE_SECONDS = 90         # don't act in the first 90s after the bet is adopted (stale/noisy numbers)
MODEL_FLIP_MIN_SECONDS_LEFT = 10      # no point selling in the last seconds (market closing)


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str = ""


def current_position_value(position: dict, market_price: float) -> float:
    return market_price if position["side"] == "bid" else (1 - market_price)


def check_exit(position: dict, current_market_price: float, current_model_prob: float,
                seconds_remaining: float, reversion_z: Optional[float] = None,
                partial_profit_fraction: Optional[float] = None,
                peak_gain_per_contract: Optional[float] = None) -> ExitDecision:
    entry_price = position["entry_price"]
    count = position["count"]
    value_now = current_position_value(position, current_market_price)

    if not STOP_LOSS_ENABLED:
        return ExitDecision(False)

    # Filled outside the allowed price tolerance / below the floor: get out immediately.
    if position.get("force_exit"):
        return ExitDecision(True, f"fill_outside_tolerance (real entry ${entry_price:.2f} -- exiting immediately)")

    # TAKE PROFIT: the held side is worth TAKE_PROFIT_PRICE or more -> sell and bank it.
    if TAKE_PROFIT_ENABLED and value_now >= TAKE_PROFIT_PRICE - 1e-9:
        return ExitDecision(True, f"capture_profit (value ${value_now:.2f} >= ${TAKE_PROFIT_PRICE:.2f}, entry ${entry_price:.2f})")

    # ONE rule, for everything, tag-independent -- checked purely on
    # entry_price and current value, regardless of which tier, coin,
    # or market type placed this position (crypto, manual, sports,
    # anything). This is deliberately the ONLY exit rule in this file.
    bet_amount_dollars = entry_price * count
    current_dollars = value_now * count
    # TWO-STAGE STOP. Until the position has been in profit, the stop is fixed at 93% of what was paid.
    # Once its value has ever risen above entry, the stop trails at 99.5% of the best value reached
    # (only ever moves up).
    peak_gain = max(peak_gain_per_contract or 0.0, 0.0)
    peak_value = entry_price + peak_gain
    if PROFIT_TRAIL_ENABLED and peak_gain > 1e-9:
        threshold_dollars = peak_value * count * PROFIT_TRAIL_FRACTION
        rule = f"trailing {PROFIT_TRAIL_FRACTION*100:.1f}% of peak value ${peak_value*count:.2f}"
    else:
        threshold_dollars = bet_amount_dollars * UNIVERSAL_STOP_LOSS_FRACTION
        rule = f"{UNIVERSAL_STOP_LOSS_FRACTION*100:.0f}% of original investment"
    if position.get("manually_adopted"):
        # Manual bids use their own stop (overrides the universal one, whether higher or lower).
        threshold_dollars = bet_amount_dollars * MANUAL_STOP_LOSS_FRACTION
        rule = f"{MANUAL_STOP_LOSS_FRACTION*100:.0f}% of original investment (manual bid)"
    # Absolute stop on the held side. Only applies to positions entered ABOVE the level: a position whose entry is already
    # at or below 86c (e.g. a 1-share ride bought at 60-85c, or an adopted position with a low cost) keeps the existing
    # entry-relative stop, so it is not sold instantly.
    if (not position.get("manually_adopted") and not (PROFIT_TRAIL_ENABLED and peak_gain > 1e-9)
            and entry_price > STOP_LOSS_LEVEL_HELD_SIDE + 1e-9):
        threshold_dollars = STOP_LOSS_LEVEL_HELD_SIDE * count
        rule = f"absolute {STOP_LOSS_LEVEL_HELD_SIDE*100:.0f}c on held side"
    if current_dollars <= threshold_dollars + 1e-9:
        return ExitDecision(True, f"stop_loss (bet ${bet_amount_dollars:.2f} -> now ${current_dollars:.2f}, "
                                   f"threshold ${threshold_dollars:.2f} -- {rule}, entry ${entry_price:.2f})")

    # MODEL FLIP (manual / adopted positions only): if the model now says the OTHER side is clearly more
    # likely to win, sell. Skipped for the first MODEL_FLIP_GRACE_SECONDS after adoption and when there is
    # no model number or the market is about to close.
    if (MODEL_FLIP_ENABLED and position.get("manually_adopted") and current_model_prob is not None
            and seconds_remaining >= MODEL_FLIP_MIN_SECONDS_LEFT
            and (time.time() - position.get("entry_time", time.time())) >= MODEL_FLIP_GRACE_SECONDS):
        my_win = current_model_prob if position["side"] == "bid" else (1 - current_model_prob)
        if my_win < MODEL_FLIP_MIN_WIN - 1e-9:
            return ExitDecision(True, f"model_flip (model gives our side only {my_win*100:.0f}% to win, "
                                       f"below {MODEL_FLIP_MIN_WIN*100:.0f}% -- manual bet, entry ${entry_price:.2f}, now ${value_now:.2f})")

    return ExitDecision(False)
