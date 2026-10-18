"""
Early-exit logic for open positions, separate from entry logic on purpose.

ONE stop-loss rule for every position, both sides:
    exit when the position's value falls to 86% of what was paid or lower
    (a 14% loss). A $10 bet exits no later than $8.60.

"Value" is always the price of the side we HOLD:
    bid (YES) position -> value = market_price (the YES price)
    NO position        -> value = 1 - market_price

So the same 86% / 14% rule protects YES and NO. In YES-price terms, a NO
position exits when the YES price rises; the math below handles that.
"""
import time
from dataclasses import dataclass
from typing import Optional
from apply_real_fees import fee_per_contract  # noqa: F401  (kept from original)

STOP_LOSS_ENABLED = True

# Stop levels. LOSS_PCT is the max loss; EXIT_FRACTION is the same thing
# expressed as "exit when value <= this share of what was paid".
STOP_LOSS_PCT = 0.14
STOP_EXIT_FRACTION = 1.0 - STOP_LOSS_PCT          # 0.86

# Separate knobs per side, both default to the same 86% exit.
YES_STOP_EXIT_FRACTION = STOP_EXIT_FRACTION       # YES / bid positions
NO_STOP_EXIT_FRACTION = STOP_EXIT_FRACTION        # NO positions
MANUAL_STOP_EXIT_FRACTION = STOP_EXIT_FRACTION    # manually adopted positions (override)

# PRICE STOP (both sides): also exit when the side we hold is priced at 86c or lower -- i.e. the OTHER side has
# climbed to 14c or higher. For a NO bet bought at 96c (= selling YES at 4c) that is YES reaching 14c.
# Whichever of the two stops (86% of what was paid, or held side <= 86c) comes first wins.
# Only applies when we paid more than 86c, so a cheaper entry isn't sold the moment it fills.
PRICE_STOP_HELD = 0.86                            # held side's price at/below this -> exit
PRICE_STOP_OTHER = 1.0 - PRICE_STOP_HELD          # = other side at/above 14c

TAKE_PROFIT_ENABLED = False
TAKE_PROFIT_PRICE = 0.85      # sell when the held side's value reaches this
MODEL_FLIP_ENABLED = True     # manual bets: sell if the model clearly favors the other side
MODEL_FLIP_MIN_WIN = 0.35     # sell when the model's win chance for OUR side drops below this
MODEL_FLIP_GRACE_SECONDS = 90
MODEL_FLIP_MIN_SECONDS_LEFT = 10


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str = ""


def is_yes_side(position: dict) -> bool:
    return position["side"] == "bid"


def current_position_value(position: dict, market_price: float) -> float:
    """Price of the side we hold. market_price is the YES price."""
    return market_price if is_yes_side(position) else (1 - market_price)


def stop_exit_fraction(position: dict) -> float:
    if position.get("manually_adopted"):
        return MANUAL_STOP_EXIT_FRACTION
    return YES_STOP_EXIT_FRACTION if is_yes_side(position) else NO_STOP_EXIT_FRACTION


def check_exit(position: dict, current_market_price: float, current_model_prob: float,
               seconds_remaining: float, reversion_z: Optional[float] = None,
               partial_profit_fraction: Optional[float] = None,
               peak_gain_per_contract: Optional[float] = None) -> ExitDecision:
    entry_price = position["entry_price"]   # what we paid for the side we hold
    count = position["count"]
    value_now = current_position_value(position, current_market_price)

    if not STOP_LOSS_ENABLED:
        return ExitDecision(False)

    # Filled outside the allowed price tolerance / below the floor: get out immediately.
    if position.get("force_exit"):
        return ExitDecision(True, f"fill_outside_tolerance (real entry ${entry_price:.2f} -- exiting immediately)")

    # TAKE PROFIT (off by default)
    if TAKE_PROFIT_ENABLED and value_now >= TAKE_PROFIT_PRICE - 1e-9:
        return ExitDecision(True, f"capture_profit (value ${value_now:.2f} >= ${TAKE_PROFIT_PRICE:.2f}, entry ${entry_price:.2f})")

    # STOP LOSS: same rule for YES and NO, on the held side's value.
    frac = stop_exit_fraction(position)
    bet_amount_dollars = entry_price * count
    current_dollars = value_now * count
    threshold_dollars = bet_amount_dollars * frac
    if entry_price > PRICE_STOP_HELD + 1e-9 and value_now <= PRICE_STOP_HELD + 1e-9:
        side = "YES" if is_yes_side(position) else "NO"
        other = "NO" if side == "YES" else "YES"
        return ExitDecision(True, f"stop_loss_price ({side} now ${value_now:.2f} <= ${PRICE_STOP_HELD:.2f}, i.e. {other} at "
                                  f"${1 - value_now:.2f} >= ${PRICE_STOP_OTHER:.2f}; entry ${entry_price:.2f})")
    if current_dollars <= threshold_dollars + 1e-9:
        side = "YES" if is_yes_side(position) else "NO"
        return ExitDecision(True, f"stop_loss ({side}: bet ${bet_amount_dollars:.2f} -> now ${current_dollars:.2f}, "
                                  f"threshold ${threshold_dollars:.2f} = {frac*100:.0f}% of investment, "
                                  f"entry ${entry_price:.2f}, value ${value_now:.2f})")

    # MODEL FLIP (manual / adopted positions only)
    if (MODEL_FLIP_ENABLED and position.get("manually_adopted") and current_model_prob is not None
            and seconds_remaining >= MODEL_FLIP_MIN_SECONDS_LEFT
            and (time.time() - position.get("entry_time", time.time())) >= MODEL_FLIP_GRACE_SECONDS):
        my_win = current_model_prob if is_yes_side(position) else (1 - current_model_prob)
        if my_win < MODEL_FLIP_MIN_WIN - 1e-9:
            return ExitDecision(True, f"model_flip (model gives our side only {my_win*100:.0f}% to win, "
                                      f"below {MODEL_FLIP_MIN_WIN*100:.0f}% -- manual bet, entry ${entry_price:.2f}, now ${value_now:.2f})")

    return ExitDecision(False)
