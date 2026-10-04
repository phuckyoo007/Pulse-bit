"""
Real Kalshi trading fee formula.
"""
import math

FEE_MULTIPLIER = 0.07


def fee_per_contract(price: float) -> float:
    """Kalshi's real per-contract fee: 0.07 * price * (1 - price), rounded
    up to the nearest cent."""
    raw_fee = FEE_MULTIPLIER * price * (1 - price)
    return math.ceil(raw_fee * 100) / 100
