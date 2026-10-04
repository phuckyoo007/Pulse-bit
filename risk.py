"""
Position sizing and bankroll risk controls. Separate from edge detection
on purpose — how much to risk is a different question from whether
there's an edge at all, and conflating them makes both harder to reason
about.
"""
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class RiskParams:
    kelly_fraction: float = 0.25        # fractional Kelly — full Kelly is too aggressive for real bankrolls
    min_edge_pct: float = 5.0           # minimum |edge| (percentage points) to act on at all
    max_position_pct: float = 5.0       # hard cap on % of bankroll in any one market, regardless of Kelly
    max_open_positions: int = 10
    max_daily_loss_pct: float = 8.0     # halt new entries for the day past this drawdown
    max_trusted_edge_pct: float = 25.0  # edges beyond this are sized AS IF they were this large, not their
                                         # full raw size — an oversized edge isn't more trustworthy just
                                         # because it looks bigger; the model's sensitivity to small errors
                                         # grows sharply for unusually large edges (verified mathematically),
                                         # and real data showed this exact bucket underperforming twice in a
                                         # row. This still trades large edges, just doesn't let Kelly size
                                         # them as aggressively as the raw number would suggest.


def kelly_size(my_prob: float, market_price: float, bankroll: float, params: RiskParams) -> float:
    """
    Returns the dollar amount to risk, given your probability estimate
    `my_prob` vs the market's YES price `market_price` (in dollars, e.g.
    0.42), using fractional Kelly. Returns 0 if the edge doesn't clear
    min_edge_pct.

    Handles BOTH directions: if my_prob > market_price, sizes a bet on
    YES; if my_prob < market_price, sizes a bet on NO (transforming to
    NO's own price and probability first). An earlier version of this
    function only ever computed Kelly for YES, which silently returned 0
    any time the correct bet was actually on NO — worth knowing about if
    you're comparing against old behavior.

    Kelly formula for a binary bet with payout odds b = (1 - price) / price:
        f* = (b*p - q) / b       where p = win probability, q = 1 - p
    """
    edge_pct = (my_prob - market_price) * 100
    if abs(edge_pct) < params.min_edge_pct:
        return 0.0
    if market_price <= 0 or market_price >= 1:
        return 0.0

    if my_prob >= market_price:
        price, p = market_price, my_prob          # betting YES
    else:
        price, p = 1 - market_price, 1 - my_prob  # betting NO -- flip to NO's own price/probability

    # Cap the probability gap actually used for sizing -- direction was
    # already decided above from the real my_prob, this only moderates
    # HOW MUCH gets risked when the apparent edge is unusually large.
    max_gap = params.max_trusted_edge_pct / 100
    if (p - price) > max_gap:
        p = price + max_gap

    b = (1 - price) / price
    q = 1 - p
    f_star = (b * p - q) / b

    if f_star <= 0:
        return 0.0

    fraction = f_star * params.kelly_fraction
    fraction = min(fraction, params.max_position_pct / 100)
    return round(bankroll * fraction, 2)


class CircuitBreaker:
    """Tracks realized PnL for the current UTC day; halts new entries past
    a drawdown threshold. Call record_pnl() after every settled position."""

    def __init__(self, starting_bankroll: float, params: RiskParams):
        self.starting_bankroll = starting_bankroll
        self.params = params
        self._day = datetime.now(timezone.utc).date()
        self._day_pnl = 0.0

    def _roll_day_if_needed(self):
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day = today
            self._day_pnl = 0.0

    def prime_from_settlements(self, settlements: list):
        """Call once at startup to recover today's already-realized PnL
        from a persisted settlements list. Without this, restarting the
        bot mid-day resets day_pnl to 0 and forgets any losses that
        already happened earlier that same day -- meaning a second round
        of losses after a restart could exceed max_daily_loss_pct without
        ever tripping, since the breaker has no memory of the first round.

        Only counts entries explicitly marked dry_run=False -- older
        settlement records from before that field existed default to
        being EXCLUDED, not included. Including anything uncertain by
        default is exactly what caused a real bug: old test/phantom-
        bankroll settlements got silently summed into "today's real PnL"
        and produced a nonsense number next to a real, tiny balance."""
        today = datetime.now(timezone.utc).date()
        todays_pnl = sum(
            s["pnl"] for s in settlements
            if s.get("dry_run") is False
            and datetime.fromtimestamp(s["timestamp"], tz=timezone.utc).date() == today
        )
        self._day = today
        self._day_pnl = todays_pnl

    def record_pnl(self, pnl: float):
        self._roll_day_if_needed()
        self._day_pnl += pnl

    def tripped(self) -> bool:
        self._roll_day_if_needed()
        loss_pct = -self._day_pnl / self.starting_bankroll * 100
        return loss_pct >= self.params.max_daily_loss_pct

    @property
    def day_pnl(self) -> float:
        self._roll_day_if_needed()
        return self._day_pnl
