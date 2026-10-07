"""
Early-exit logic for open positions, separate from entry logic on purpose.

SIMPLIFIED, per explicit request -- ONE uniform rule for everything: if
a position has lost 8% of what was paid for it (value has fallen to
92% of entry or below), exit. No per-tier variation, no trailing/peak
logic, no exceptions. A $10 bet exits no later than $9.20.
"""
from dataclasses import dataclass
from typing import Optional
from apply_real_fees import fee_per_contract

STOP_LOSS_ENABLED = True
UNIVERSAL_STOP_LOSS_FRACTION = 0.88   # initial stop: exit when value falls to <= 88% of what was paid
PROFIT_TRAIL_ENABLED = False          # OFF: no trailing stop -- the stop stays fixed at UNIVERSAL_STOP_LOSS_FRACTION of what was paid
PROFIT_TRAIL_FRACTION = 0.97          # once a position has gone into profit: exit if value falls to <= 97% of its peak
MANUAL_STOP_LOSS_FRACTION = 1.00      # manually placed (adopted) positions: exit as soon as value is <= 100% of what was paid


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
        manual_floor = bet_amount_dollars * MANUAL_STOP_LOSS_FRACTION
        if manual_floor > threshold_dollars:
            threshold_dollars = manual_floor
            rule = f"{MANUAL_STOP_LOSS_FRACTION*100:.0f}% of original investment (manual bid)"
    if current_dollars <= threshold_dollars + 1e-9:
        return ExitDecision(True, f"stop_loss (bet ${bet_amount_dollars:.2f} -> now ${current_dollars:.2f}, "
                                   f"threshold ${threshold_dollars:.2f} -- {rule}, entry ${entry_price:.2f})")

    return ExitDecision(False)
