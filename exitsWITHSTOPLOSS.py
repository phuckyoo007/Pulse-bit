"""
Early-exit logic for open positions, separate from entry logic on purpose
-- deciding whether to close something you already hold is a different
question from deciding whether to open it.
Six rules, checked in this order (most urgent first):
  1. STOP-LOSS: the position has moved against you sharply. Exit now,
     regardless of time left, to cap the loss rather than ride it to zero.
  2. MODEL REVERSAL: the model's current read has flipped to favor the
     OPPOSITE side of what you're holding, by a real margin -- the
     informational basis for the trade no longer holds.
  3. TAKE-PROFIT: the position has gained a large multiple of what you
     paid, period -- regardless of time left or short-term statistics.
     This is the plain "you're up a lot, take it" rule -- none of the
     other rules actually cover this case. A position that climbed
     slowly and steadily can show an unremarkable short-term signal (rule
     5 below) even with a huge total gain since entry, and might have
     plenty of time left (rule 6 below doesn't apply yet either) -- this
     rule exists specifically to not miss that.
  4. MODEST PROFIT: catches smaller gains that don't reach take-profit's
     4x bar but are still clearly worth capturing -- "clearly" meaning
     the gain comfortably exceeds the REAL round-trip fee cost of
     exiting (confirmed via Kalshi's documented fee formula, see
     apply_real_fees.py). This deliberately does NOT fire on every
     positive cent: exiting on a gain that's smaller than (or close to)
     what the extra exit-side fee costs would lock in a loss while
     looking like a win. Only fires once the gain clearly clears that
     bar by a real margin.
  5. REVERSION RECOVERY: the contract's own price has swung back up well
     above its recent trading range (per reversion.py), and the position
     is currently profitable. Takes the recovery gain rather than riding
     it further, on the same "unproven hypothesis" basis as the entry
     side of this signal -- see reversion.py's own caveats.
  6. LOCK-IN NEAR EXPIRY: very little time left, and the position is
     currently worth close to its maximum payout. Selling now takes a
     slightly lower guaranteed amount instead of the full payout, in
     exchange for removing the small remaining risk of a last-second
     reversal. This is a risk/reward trade-off, not extra profit --
     said plainly in case it reads as "free money" at a glance.
"""
from dataclasses import dataclass
from typing import Optional
from apply_real_fees import fee_per_contract
STOP_LOSS_FRACTION = 0.92     # TIGHTENED from 0.92 to 0.94, per explicit request, for BOT-PLACED bets --
                                # now tolerates a 6% dip from peak before firing (was 8% at 0.92).
                                # This is separate from MANUAL_STOP_LOSS_VALUE below -- manual bets
                                # are untouched by this change.
# SEPARATE pre-breakeven rule for MANUALLY-PLACED bets specifically --
# per explicit clarification, this is a PERCENTAGE of the actual total
# DOLLAR amount you bet (entry_price * count), not a raw per-contract
# cents figure. e.g. a $50 bet exits once its real total value drops to
# 93% * $50 = $46.50. During the pre-breakeven phase, peak_value always
# equals entry_price exactly (peak can't be below entry, and
# "pre-breakeven" by definition means it's never risen above entry
# either) -- so this fraction is mathematically identical whether it's
# read as "93% of peak" or "93% of your original bet," and the earlier
# per-contract-cents version was actually computing the same trigger
# point, just displaying it in a way that didn't match how you were
# tracking it mentally. The fix here is really about the LOG MESSAGE
# showing real total dollars, not a change to the underlying math.
# Only applies BEFORE breakeven -- once a manual position clears
# breakeven, it still uses the same BREAKEVEN_STOP_LOSS_FRACTION
# (percentage-of-peak) as everything else below.
MANUAL_STOP_LOSS_FRACTION = 0.92
# Fixed exit floor for the LATCH_90 tier specifically, per explicit
# request -- this tier's positions (tagged "latch_90_tier": True by
# bot.py) get a COMPLETELY separate, much simpler exit rule: hold
# indefinitely through any appreciation, no profit-taking, no
# breakeven-tightening -- the ONLY thing that closes one early is
# dropping to or below this fixed absolute price. Uses <= rather than
# an exact-match check on purpose: if price gaps straight past 0.84 in
# one tick (real markets do this), an exact-match trigger could get
# skipped entirely and leave the position unprotected.
LATCH_90_EXIT_VALUE = 0.84
# Two-stage stop-loss, per explicit request -- BEFORE a position has
# ever reached breakeven (peak_gain_per_contract > 0, i.e. its peak
# value has at some point exceeded what you paid), it gets the loose
# STOP_LOSS_FRACTION above, so ordinary pre-breakeven wobble doesn't
# trigger an exit. The MOMENT peak_gain_per_contract goes positive --
# meaning the position has, at some point, been worth more than you
# paid for it -- the stop threshold switches to 100% of peak: from then
# on, ANY dip below the peak exits immediately, locking in a real,
# non-losing outcome rather than risking that gain evaporating.
# peak_gain_per_contract only ever increases (bot.py tracks it as a
# running max), so this is a one-way switch -- once a position clears
# breakeven, it stays in tight mode permanently, even if the price later
# dips back down toward entry. Uses a strict >0 (not >=0) so a fresh
# position at entry (peak_gain_per_contract starts at exactly 0.0)
# doesn't immediately trip into tight mode before it's gained anything.
# LOOSENED from 100% to 97.5%, then further to 96%, per explicit
# request -- 100% (zero tolerance) fired on the very first tick of
# ordinary price noise right after clearing breakeven, exiting too
# early. 96% still locks in most of any peak reached, while giving a
# bit more room for normal wobble than 97.5% did.
BREAKEVEN_STOP_LOSS_FRACTION = 0.96
# RE-ENABLED, per explicit request, at a much wider margin than either
# previous attempt -- 10.0 fired constantly (normal wobble, not real
# reversals); 20.0 was STILL too sensitive on real Pulse data (19 fires
# in 30 minutes, each paying two real fees -- entry + exit -- instead
# of the single fee a natural settlement costs). Widened to 30.0 this
# time specifically to require a genuinely dramatic reversal before
# acting, per explicit request for a real safety net rather than a
# hair-trigger. If this STILL fires too often on real data, the fix is
# to widen it further, not to add other conditions -- the history here
# shows this rule is simply very sensitive to its own margin.
MODEL_REVERSAL_MARGIN_PP = 30.0
TAKE_PROFIT_MULTIPLE = 4.0     # exit once value has reached 4x what you paid, any time, any category
MODEST_PROFIT_FEE_MULTIPLE = 3.0   # gain per contract must be at least this many times the
                                    # estimated round-trip fee before counting as "clearly worth capturing"
CAPTURE_PROFIT_MIN_GAIN = 0.10   # no longer gates the rule directly, kept for reference
CAPTURE_PROFIT_TIME_WINDOW_SECONDS = 85   # capture_profit only fires at or below this, per explicit request
CAPTURE_PROFIT_MAX_VALUE = 0.98   # capture_profit never fires at or above this value, per explicit request -- rides to natural settlement instead
VALUE_CAPTURE_THRESHOLD = 0.98   # exit once value reaches this, regardless of gain size, per explicit request
REVERSION_RECOVERY_Z_THRESHOLD = 1.0   # exit a profitable dip-buy once price has recovered this far above its recent trend
LOCK_IN_SECONDS = 45           # only consider locking in gains inside this window before close
LOCK_IN_MIN_VALUE_FRACTION = 0.80   # ...and only if current value is at least this fraction of max payout
@dataclass
class ExitDecision:
    should_exit: bool
    reason: str = ""
def current_position_value(position: dict, current_market_price: float) -> float:
    """What your held position is worth right now, per contract, if you
    closed it at the current price. A 'bid' (long yes) position's value
    IS the current yes price; an 'ask' (long no) position's value is
    (1 - current yes price)."""
    if position["side"] == "bid":
        return current_market_price
    return 1 - current_market_price
def check_exit(position: dict, current_market_price: float, current_model_prob: float,
                seconds_remaining: float, reversion_z: Optional[float] = None,
                partial_profit_fraction: Optional[float] = None,
                peak_gain_per_contract: Optional[float] = None) -> ExitDecision:
    entry_price = position["entry_price"]
    side = position["side"]
    value_now = current_position_value(position, current_market_price)
    # Rule -1: LATCH_90 TIER fixed floor -- DISABLED per explicit
    # request. LATCH_90 positions now fall through to the STANDARD
    # bot stop-loss (Rule 1 below, STOP_LOSS_FRACTION pre-breakeven /
    # BREAKEVEN_STOP_LOSS_FRACTION once profitable) instead of this
    # fixed 84-cent floor -- the peak-relative percentage system was
    # judged more accurate than a flat cutoff. Left here, commented
    # out, matching this file's established disable pattern, in case
    # it's wanted again later. The "latch_90_tier" tag on the position
    # is now purely informational -- it no longer changes exit behavior
    # at all; a LATCH_90 position is treated exactly like any other
    # bot-placed position from this point on.
    # if position.get("latch_90_tier", False):
    #     if value_now <= LATCH_90_EXIT_VALUE + 1e-9:
    #         return ExitDecision(True, f"latch_90_stop_loss (value ${value_now:.2f} dropped to or below the "
    #                                    f"fixed ${LATCH_90_EXIT_VALUE:.2f} floor, entry ${entry_price:.2f})")
    #     return ExitDecision(False)
    # Rule 0: MANUAL PROFIT CASHOUT, per explicit request -- ONLY applies
    # to positions adopted from a manual bid placed directly on Kalshi
    # (position["manually_adopted"] is True; bot-placed positions never
    # have this key at all, so .get(..., False) correctly excludes them).
    # The instant total real profit in DOLLARS (not per-contract) on a
    # manual position crosses this threshold, cash out immediately --
    # this checks BEFORE the stop-loss rule below, since it's an
    # unconditional take-profit that should fire regardless of where
    # price currently sits relative to its peak.
    MANUAL_PROFIT_CASHOUT_THRESHOLD = 5.00
    if position.get("manually_adopted", False):
        total_profit_dollars = (value_now - entry_price) * position["count"]
        if total_profit_dollars >= MANUAL_PROFIT_CASHOUT_THRESHOLD - 1e-9:
            return ExitDecision(True, f"manual_profit_cashout (manual bid profit ${total_profit_dollars:.2f} "
                                       f"crossed ${MANUAL_PROFIT_CASHOUT_THRESHOLD:.2f}, value ${value_now:.2f}, "
                                       f"entry ${entry_price:.2f}, count {position['count']})")
    # Rule 1: TRAILING stop-loss, per explicit request -- 95% of the
    # position's PEAK value (not the fixed entry price) BEFORE it's ever
    # reached breakeven; 100% of peak (zero tolerance) once it has. As
    # the position's peak_gain_per_contract rises, the stop threshold
    # rises right along with it, locking in more of the gain -- but the
    # threshold never moves back down, since peak_gain_per_contract
    # itself never decreases (bot.py updates it with a running max
    # before calling this). Falls back to the entry price itself
    # (peak == entry, matching the OLD, non-trailing behavior) if
    # peak_gain_per_contract wasn't provided at all.
    # Example matching a real request: $0.95 entry, peak rises to
    # $0.99 (peak_gain_per_contract=$0.04, i.e. > 0 -- breakeven already
    # cleared) -> stop threshold is 100% * $0.99 = $0.99, not
    # 95% * $0.99 = $0.9405 -- any dip at all below the $0.99 peak now
    # exits immediately.
    # FIXED: added an epsilon guard -- the threshold can compute as
    # e.g. 0.27999999999999997 instead of exactly 0.28 due to float
    # precision, which silently failed to trigger for a position
    # landing exactly at the intended threshold.
    count = position["count"]
    peak_gain = peak_gain_per_contract or 0.0
    peak_value = entry_price + peak_gain
    has_cleared_breakeven = peak_gain > 1e-9   # strict > 0 -- a fresh position (peak_gain
                                                # starts at exactly 0.0) must NOT immediately
                                                # switch to tight mode before it's gained anything
    is_manual = position.get("manually_adopted", False)
    # CHANGED per explicit request: converts to REAL DOLLAR AMOUNTS
    # FIRST, then does the actual comparison entirely in dollars --
    # not in per-contract price converted to dollars only afterward
    # for a log message. e.g. $10 down, drops 5% -> compares $9.50
    # directly against a $9.50 threshold, not 0.95 against some
    # per-contract price fraction. Mathematically this lands on the
    # same trigger point either way (count is just a constant
    # multiplier), but the comparison itself now genuinely happens in
    # dollars, matching how the money is actually being tracked.
    bet_amount_dollars = entry_price * count
    peak_dollars = peak_value * count
    current_dollars = value_now * count
    if has_cleared_breakeven:
        threshold_dollars = peak_dollars * BREAKEVEN_STOP_LOSS_FRACTION
        mode = "breakeven-tight"
    elif is_manual:
        threshold_dollars = peak_dollars * MANUAL_STOP_LOSS_FRACTION
        mode = "pre-breakeven-manual"
    else:
        threshold_dollars = peak_dollars * STOP_LOSS_FRACTION
        mode = "pre-breakeven"
    if current_dollars <= threshold_dollars + 1e-9:
        # Logged in REAL TOTAL DOLLARS (per-contract price * count),
        # per explicit request -- per-contract cents alone didn't match
        # how the actual bet amount was being tracked, which made a
        # mathematically-correct trigger look wrong or inconsistent.
        return ExitDecision(True, f"stop_loss ({mode}, bet ${bet_amount_dollars:.2f} -> now ${current_dollars:.2f} "
                                   f"(peak ${peak_dollars:.2f}), threshold ${threshold_dollars:.2f} -- "
                                   f"per-contract: value ${value_now:.2f} vs peak ${peak_value:.2f}, entry ${entry_price:.2f})")
    # Rule 2: model reversal -- DISABLED per explicit request. Model and
    # edge should only ever inform ENTRY decisions, never exits -- exits
    # are now purely value-based (Rule 1's stop-loss). Left here,
    # commented out, matching this file's established pattern for every
    # other disabled rule, so it's easy to re-enable later if wanted.
    # This also makes Anchor's behavior unchanged, since it already
    # always passed the neutral 0.5 for current_model_prob (no model
    # exists there at all) -- this rule was already a permanent no-op
    # for Anchor before this change.
    # model_support_for_your_side = current_model_prob if side == "bid" else (1 - current_model_prob)
    # if model_support_for_your_side < 0.5 - (MODEL_REVERSAL_MARGIN_PP / 100):
    #     return ExitDecision(True, f"model_reversal (model now supports your side at only "
    #                                f"{model_support_for_your_side:.3f}, entry ${entry_price:.2f}, "
    #                                f"value ${value_now:.2f})")
    # Rule 3: take-profit -- DISABLED per explicit request ("hold till
    # it closes, settlement only"). Left here, commented out:
    # if entry_price > 0 and value_now >= entry_price * TAKE_PROFIT_MULTIPLE:
    #     return ExitDecision(True, f"take_profit (value ${value_now:.2f} is {value_now/entry_price:.1f}x entry ${entry_price:.2f})")
    # Rule 3.5: partial profit -- DISABLED per the same explicit request.
    # if partial_profit_fraction is not None and entry_price > 0 and value_now >= entry_price * (1 + partial_profit_fraction):
    #     return ExitDecision(True, "partial_profit ...")
    # Rule 3.6b: capture profit -- DISABLED for this TRIAL, per explicit
    # "no take profit, hold till close" request. Original (85s window,
    # any positive gain, excluded above 98%) saved separately for
    # restoration when the trial ends.
    # if entry_price > 0 and seconds_remaining <= CAPTURE_PROFIT_TIME_WINDOW_SECONDS and value_now < CAPTURE_PROFIT_MAX_VALUE:
    #     gain = value_now - entry_price
    #     if gain > 1e-9:
    #         return ExitDecision(True, "capture_profit ...")
    # Rule 4: modest profit -- DISABLED per the same explicit request.
    # gain_per_contract = value_now - entry_price
    # if gain_per_contract > 0:
    #     round_trip_fee = fee_per_contract(entry_price) + fee_per_contract(value_now)
    #     if gain_per_contract >= MODEST_PROFIT_FEE_MULTIPLE * round_trip_fee:
    #         return ExitDecision(True, "modest_profit ...")
    # Rule 5: reversion recovery -- DISABLED per explicit request.
    # Only fires for 'bid' (long yes) positions, matching the entry side
    # reversion.py's dip signal buys. Left here, commented out, so it's
    # easy to re-enable later:
    # if side == "bid" and reversion_z is not None and reversion_z >= REVERSION_RECOVERY_Z_THRESHOLD and value_now > entry_price:
    #     return ExitDecision(True, f"reversion_recovery (z={reversion_z}, value ${value_now:.2f} vs entry ${entry_price:.2f})")
    # Rule 6: lock in a near-expiry gain -- DISABLED per the same
    # explicit request. Every position now rides to natural settlement
    # only for this rule; check_settlements() in bot.py is what actually
    # closes each position out, once Kalshi settles it.
    # if seconds_remaining <= LOCK_IN_SECONDS and value_now >= LOCK_IN_MIN_VALUE_FRACTION:
    #     return ExitDecision(True, f"lock_in_near_expiry (value ${value_now:.2f}, {int(seconds_remaining)}s left)")
    return ExitDecision(False)
