"""
Kalshi crypto scalper — trades KXBTC15M-style up/down contracts using a
volatility-priced probability model instead of guessing from price alone.
"""
import json
import os
import sys
import time
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
from pathlib import Path
import requests
from kalshi_client import KalshiClient
from price_feed import pyth_key_configured
from strategy import discover_crypto_markets, evaluate_crypto_market, \
    discover_generic_short_markets, evaluate_generic_market, \
    discover_commodity_markets, evaluate_commodity_market, discover_fx_markets, \
    discover_index_markets, evaluate_index_market, GENERIC_MAX_SECONDS_REMAINING
from priority import RankedCandidate, potential_points, rank_candidates, size_scale_factor_91_95
from risk import RiskParams, CircuitBreaker, kelly_size
from exits import check_exit, current_position_value, ExitDecision, instant_stop_decision, INSTANT_STOP_FRACTION
import flip_star
import pulse_v2
import markets_paper
import ride
import build_dashboard_data
import reversion
import price_feed
import squeeze_momentum
try:
    import trend_report
except Exception as _e:   # optional module: the bot must never fail to start because of it
    trend_report = None
    print(f"[TREND] trend_report.py not loaded ({_e}); trend logging disabled")
import shadow_exit_tracking as sht

DRY_RUN = False
EXIT_DRY_RUN = False
POLL_SECONDS = 1
STARTING_BANKROLL = 10.0
MIN_BANKROLL_FLOOR = 0.0
DAILY_PROFIT_TARGET = 2000.0
RISK_PARAMS = RiskParams(
    kelly_fraction=0.15,
    min_edge_pct=0.0,
    max_position_pct=3.0,
    max_open_positions=10000,
    max_daily_loss_pct=20.0,
)
BTC_87_THRESHOLD_SECONDS = 105
BTC_87_MIN_PRICE = 0.98
BTC_87_SHARES = 5
XRP_87_THRESHOLD_SECONDS = 105
XRP_87_MIN_PRICE = 0.98
XRP_87_SHARES = 5
SOL_87_THRESHOLD_SECONDS = 105
SOL_87_MIN_PRICE = 0.98
SOL_87_SHARES = 5
DOGE_87_THRESHOLD_SECONDS = 105
DOGE_87_MIN_PRICE = 0.98
DOGE_87_SHARES = 5
BNB_87_THRESHOLD_SECONDS = 105
BNB_87_MIN_PRICE = 0.98
BNB_87_SHARES = 5
BCH_87_THRESHOLD_SECONDS = 105
BCH_87_MIN_PRICE = 0.98
BCH_87_SHARES = 5
ETH_87_THRESHOLD_SECONDS = 105
ETH_87_MIN_PRICE = 0.98
ETH_87_SHARES = 5
HYPE_87_THRESHOLD_SECONDS = 105
HYPE_87_MIN_PRICE = 0.98
HYPE_87_SHARES = 5
LATCH2_90_THRESHOLD_SECONDS = 105
LATCH2_90_MIN_PRICE = 0.98
LATCH2_90_SHARES = 5
TRIPLE_90_THRESHOLD_SECONDS = 105
TRIPLE_90_MIN_PRICE = 0.98
TRIPLE_90_SHARES = 5
CROSS_CONFIRM_MIN_PRICE = 0.70
TIGHT_TIME_THRESHOLD_SECONDS = 105
TIGHT_TIME_MIN_PRICE = 0.75
MIN_EDGE_FOR_87_TIERS = 1.5
EDGE_FILTER_ENABLED = False   # OFF: only the model confidence (0.82) matters
EARLY_60_THRESHOLD_SECONDS = 800
EARLY_60_MIN_PRICE = 0.58
EARLY_60_MAX_PRICE = 0.62
EARLY_60_SHARES = 5
TRIAL_SHARES_PER_TRADE = 5
ASK_MAX_PRICE = 0.99
ENTRY_MAX_SPREAD = 0.05          # skip entries when the live bid-ask gap is wider than this (thin, jumpy books)
ENTRY_MAX_PRICE = 0.99            # entry band 0.98-0.99
ENTRY_HARD_CEILING = 0.99
ENTRY_RECHECK_MIN_PRICE = 0.98   # live price must still be at/above this right before the order goes out
FILL_PRICE_TOLERANCE = 0.05      # the real fill must land within this of the intended price (and at/above the floor), else exit at once
COIN_SIZE_MULTIPLIER = {}
# Commodities (gold, silver, oil, ...) and FX (EUR/USD, GBP/USD, USD/JPY)
# 15-min markets: same rule as the crypto 87-tiers, but price-only
# (there is no vol model for these).
CFX_87_THRESHOLD_SECONDS = 105
CFX_87_MIN_PRICE = 0.98     # entry when side price is at or above this
CFX_87_SHARES = 5
# Commodities/FX now ALSO need the volatility model (Pyth price feed) to agree: model probability for the side
# above CFX_MIN_MODEL_PROB and edge for the side above CFX_MIN_EDGE_PP. If the model can't be computed
# (feed down, market closed/stale, wrong feed), the trade is SKIPPED.
CFX_MIN_MODEL_PROB = 0.82
CFX_MIN_EDGE_PP = 1.5
# Commodity/FX model + edge checks are OFF for now (see below).
# Set True to require a model number before bidding (needs a working price feed).
CRYPTO_GATE_ENABLED = True    # crypto needs side model prob > 0.99 AND side edge > +4.5pp
CRYPTO_GATE_MIN_MODEL = 0.80   # crypto entries need side model prob above this
CRYPTO_GATE_MIN_EDGE_PP = 0.0  # crypto entries need side edge above this (percentage points)
CFX_COMMODITIES_ENABLED = False   # commodities OFF
CFX_FX_ENABLED = False           # FX (EUR/USD, GBP/USD, USD/JPY) OFF
CFX_ENABLED = CFX_COMMODITIES_ENABLED or CFX_FX_ENABLED
CFX_MODEL_REQUIRED = False   # OFF: commodities/FX bid on price alone again (no price feed has worked from Railway)
_cfx_nomodel_logged = set()
# "Bid on anything" tier for crypto: t < 300s, side price > 0.83,
# model probability for that side > 0.83, and edge for that side > 1.5pp.
ANY_THRESHOLD_SECONDS = 105
ANY_MIN_PRICE = 0.98
ANY_MIN_MODEL_PROB = 0.80
ANY_MIN_EDGE_PP = 1.5
ANY_EDGE_FILTER_ENABLED = False   # edge for the side must be > ANY_MIN_EDGE_PP
ANY_SHARES = 5
GENERIC_ENTRY_MIN_PRICE = 0.97
GENERIC_ENTRY_MAX_PRICE = 0.99
GENERIC_SHARES = 5
MIN_SHARES_LIQUIDITY_REQUIRED = 5

STATE_FILE = Path("state.json")
TRADES_FILE = Path("trades.json")
SETTLEMENTS_FILE = Path("settlements.json")
SCAN_FILE = Path("scan.json")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"WARNING: {path} is corrupted ({e}) — treating it as empty rather than crashing.")
        return default


def save_json(path: Path, data):
    tmp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, default=str))
    tmp_path.replace(path)


def push_dashboard_data_to_server():
    server_url = os.environ.get("DASHBOARD_SERVER_URL")
    if not server_url:
        return
    dashboard_data_path = Path("dashboard_data.json")
    if not dashboard_data_path.exists():
        return
    try:
        headers = {"Content-Type": "application/json"}
        update_token = os.environ.get("UPDATE_TOKEN")
        if update_token:
            headers["X-Update-Token"] = update_token
        resp = requests.post(
            f"{server_url.rstrip('/')}/update",
            data=dashboard_data_path.read_bytes(),
            headers=headers,
            timeout=10,
        )
        if not resp.ok:
            print(f"Dashboard push failed ({resp.status_code}: {resp.text[:200]}) -- non-fatal, trading continues.")
    except Exception as e:
        print(f"Dashboard push failed ({e}) -- non-fatal, trading continues.")


def log_trade(result, side: str, price: float, count: float, dry_run: bool, reversion_z=None, entry_reason="model_edge"):
    trades = load_json(TRADES_FILE, [])
    trades.append({
        "timestamp": time.time(), "ticker": result.ticker, "title": result.title,
        "coin": getattr(result, "coin", None) or getattr(result, "category", None) or getattr(result, "commodity", "UNKNOWN"),
        "side": side, "price": price, "count": count,
        "edge_pct": getattr(result, "edge_pct", None), "model_prob": getattr(result, "model_prob", None),
        "seconds_remaining_at_entry": result.seconds_remaining, "dry_run": dry_run,
        "reversion_z_at_entry": reversion_z,
        "entry_reason": entry_reason,
    })
    save_json(TRADES_FILE, trades)


_filled_count = [0]
_unfilled_count = [0]
_unclear_count = [0]


def place_entry_yes(client: KalshiClient, ticker: str, price: float, count: float, dry_run: bool):
    real_side_desc = f"YES @ ${price:.2f}"
    print(f"{'DRY-RUN' if dry_run else 'PLACING'} BID {ticker} x{count} -- {real_side_desc}")
    if dry_run:
        return {"dry_run": True}
    resp = client.create_order(
        ticker=ticker, client_order_id=f"pulse-{uuid.uuid4()}",
        side="bid", count=str(count), price=f"{price:.2f}",
    )
    order = resp.get("order", {})
    status = order.get("status", "").lower()
    if status == "resting":
        try:
            positions_resp = client.get_positions()
            real_positions = positions_resp.get("market_positions", positions_resp.get("positions", []))
            actually_filled = any(
                p.get("ticker") == ticker and p.get("position", p.get("quantity", 0)) != 0
                for p in real_positions
            )
        except Exception as e:
            print(f"  Couldn't verify fill via real positions ({e}) -- order accepted but fill status unconfirmed.")
            _unclear_count[0] += 1
            return resp
        if actually_filled:
            _filled_count[0] += 1
            print(f"  CONFIRMED FILLED: {ticker} BID x{count} -- {real_side_desc} -- real position confirmed.")
        else:
            _unfilled_count[0] += 1
            print(f"  NOT YET FILLED: {ticker} BID x{count} -- {real_side_desc} is resting on the order "
                  f"book, unfilled -- this is a real, open order, not a confirmed trade yet.")
    elif status in ("filled", "executed"):
        _filled_count[0] += 1
        print(f"  CONFIRMED FILLED: {ticker} BID x{count} -- {real_side_desc} -- Kalshi reports this order as {status}.")
    else:
        _unclear_count[0] += 1
        print(f"  Fill status unclear (order status field: '{status}' -- Kalshi's exact response shape for this "
              f"wasn't independently verified). Treat this order as UNCONFIRMED until checked manually.")
    return resp


def place_entry_no(client: KalshiClient, ticker: str, price: float, count: float, dry_run: bool):
    real_side_desc = f"NO @ ${price:.2f}"
    print(f"{'DRY-RUN' if dry_run else 'PLACING'} ASK {ticker} x{count} -- {real_side_desc}")
    if dry_run:
        return {"dry_run": True}
    yes_denominated_price = 1 - price
    resp = client.create_order(
        ticker=ticker, client_order_id=f"pulse-{uuid.uuid4()}",
        side="ask", count=str(count), price=f"{yes_denominated_price:.2f}",
    )
    order = resp.get("order", {})
    status = order.get("status", "").lower()
    if status == "resting":
        try:
            positions_resp = client.get_positions()
            real_positions = positions_resp.get("market_positions", positions_resp.get("positions", []))
            actually_filled = any(
                p.get("ticker") == ticker and p.get("position", p.get("quantity", 0)) != 0
                for p in real_positions
            )
        except Exception as e:
            print(f"  Couldn't verify fill via real positions ({e}) -- order accepted but fill status unconfirmed.")
            _unclear_count[0] += 1
            return resp
        if actually_filled:
            _filled_count[0] += 1
            print(f"  CONFIRMED FILLED: {ticker} ASK x{count} -- {real_side_desc} -- real position confirmed.")
        else:
            _unfilled_count[0] += 1
            print(f"  NOT YET FILLED: {ticker} ASK x{count} -- {real_side_desc} is resting on the order "
                  f"book, unfilled -- this is a real, open order, not a confirmed trade yet.")
    elif status in ("filled", "executed"):
        _filled_count[0] += 1
        print(f"  CONFIRMED FILLED: {ticker} ASK x{count} -- {real_side_desc} -- Kalshi reports this order as {status}.")
    else:
        _unclear_count[0] += 1
        print(f"  Fill status unclear (order status field: '{status}' -- Kalshi's exact response shape for this "
              f"wasn't independently verified). Treat this order as UNCONFIRMED until checked manually.")
    return resp


_exit_sent_at = {}            # ticker -> when Kalshi last accepted an exit order for it
EXIT_IN_FLIGHT_SECONDS = 30   # a second exit for the same ticker inside this window is a duplicate -- skip it


_exit_misses = {}            # ticker -> unfilled exit tries in a row (widens the limit each time)
# 26 Oct 09:10 CT (Matthew): SPIKE GUARD. DOGE Down 7:14 CT sold at 20c in a 1-second spike and was 48c two seconds
# later; SOL 25 Oct sold at 45c while the screen showed 92c. When the book is torn open (YES bid/ask more than
# EXIT_SPIKE_SPREAD apart) a sell waits up to EXIT_SPIKE_WAIT_SECONDS for buyers to come back, re-checking every loop.
# After that it sells at whatever price is there, so a real crash still gets out a few seconds later.
EXIT_SPIKE_SPREAD = 0.15
EXIT_SPIKE_WAIT_SECONDS = 3
EXIT_INSTANT_FLOOR = 0.20     # 26 Oct 17:08 CT (Matthew): at/under 20% OF ENTRY (not 20c) the sell goes out INSTANTLY -- no spike wait
_spike_wait = {}             # ticker -> when the spike guard started holding this sell
EXIT_MAX_SLIPPAGE = 0.10   # 19 Oct (SOL 19:25 CT): never pay more than 10c past the current quote to get out

# 19 Oct 21:33 CT: in the last LAST_WINDOW_SECONDS of a round every other stop is switched off -- 6 of 7 stop-outs on
# 19 Oct were end-of-round swings on bets that went on to win. The only exit left is an immediate pull-out when the
# side we hold is at/under LAST_WINDOW_STOP_HELD (60c), i.e. the other side at/over 40c.
PROFIT_LOCK_ARM_DOLLARS = 0.25   # 21 Oct: once up this much, a Pulse bet is sold if it falls back to break-even
PROFIT_FLOOR_ARM_DOLLARS = 0.50   # 25 Oct: once up $0.50 ...
PROFIT_FLOOR_KEEP_DOLLARS = 0.25  # ... sell when the gain falls back to $0.25
TAKE_PROFIT_DOLLARS = 1.00   # 25 Oct 13:31 CT (Matthew): sell once a Pulse bet is up $1.00
LAST_WINDOW_SECONDS = 45   # 19 Oct 21:36 CT: was 30
# 26 Oct 15:12 CT (Matthew): the instant stop is 80% OF WHAT WAS PAID (a 20% loss), not an 80c price -- all round,
# last 45s included. For a NO bet it is the NO price falling to 80% of entry (= YES rising the same amount).
STOP_FRACTION_OF_ENTRY = 0.80
LAST_WINDOW_STOP_HELD = 0.80   # 26 Oct 15:09 CT: 80c in the last 45s too (ETH NO sat at 71c for 12s under the old 70c line, then fell to 13c: -$12.84)
# 21 Oct 23:10 CT (Matthew): the 70/30 and 60/40 line stops must HOLD for this many seconds before selling, so a
# one-tick spike doesn't sell a winner. The 50% crash stop is still instant. If the price climbs back above the line,
# the timer resets.
# 24 Oct 12:52 CT (Matthew): set to 0 -- the line stops now sell the instant the held side touches the line.
# (Was 4s. The HYPE stop_6040 sat under the line 6.6s and sold 68c.)
LINE_STOP_CONFIRM_SECONDS = 0
# 25 Oct 20:46 CT (Matthew: "tighten up whatever is causing the losses"). Today's end-of-market tracking showed:
#   - profit lock / profit floor sold 5 bets that went on to WIN (each sold 1-4c under entry) and saved none -> OFF
#   - 87%/86% stops: 2 of 3 sold winners (DOGE 79c, ETH 84c); the instant 80c line caught every real reversal -> OFF
#     for Pulse bets (manual bets keep their own 85% stop)
#   - sigma exit dumped SOL at 45c while the screen showed 92c -> now only fires once the price agrees (held side <= 90c)
PROFIT_LOCK_ENABLED = False
PULSE_PCT_STOPS_ENABLED = False
SIGMA_EXIT_MAX_HELD = 0.90
RED_EXIT_SECONDS = 0          # 26 Oct 19:32 CT (Matthew): no wait -- sell the moment it reads RED 3 (<0.17 sigma)
CRASH_CONFIRM_SECONDS = 4     # 25 Oct 13:27 CT (Matthew): 75c line is INSTANT; the 86%/87%-of-entry stops must hold 4s


def _held_sigma_now(ticker: str, position: dict):
    """Board 'To flip' sigma for the side this position holds (None if the board has no fresh numbers)."""
    return flip_star.held_sigma(ticker, "yes" if position.get("side") == "bid" else "no")


def _line_held_seconds(position: dict) -> float:
    """Seconds the held side has been at/under the stop line (starts the timer on first touch)."""
    since = position.setdefault("line_since", time.time())
    return time.time() - since


def _secs_left_from_market(market: dict):
    ct = market.get("close_time") or market.get("expiration_time")
    if not ct:
        return None
    try:
        return (datetime.fromisoformat(str(ct).replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return None


def place_exit(client: KalshiClient, ticker: str, close_side: str, count: float, dry_run: bool, entry_price=None):
    # Lesson from SOL 19 Oct 19:25 CT: the exit was a resting (good-till-canceled) order at 99c. On a thin book during
    # a spike it walked the book, and whatever didn't fill sat at 99c and filled there later -- the bot logged -$1.63
    # but Kalshi showed about -54%. Exits are now immediate-or-cancel, limited to 10c past the live quote. Anything
    # left unfilled is cancelled, re-adopted from Kalshi's position list next loop, and its stops fire again.
    price = 0.01 if close_side == "ask" else 0.99
    try:
        mk = client.get_market(ticker).get("market", {})
        ya, yb = mk.get("yes_ask_dollars"), mk.get("yes_bid_dollars")
        # 21 Oct (ETH 03:43 CT): three tries at "quote + 10c" all missed while the book collapsed (54c -> 10c in 12s).
        # Each unfilled try now reaches further: 10c, then 25c, then any price.
        _tries = _exit_misses.get(ticker, 0)
        _slip = EXIT_MAX_SLIPPAGE if _tries == 0 else (0.25 if _tries == 1 else 1.0)
        # what our side would sell for right now (closing YES sells at the YES bid; closing NO = buying YES at the ask)
        _our_now = (float(yb) if close_side == "ask" else 1 - float(ya)) if (ya is not None and yb is not None) else None
        if (not dry_run and ya is not None and yb is not None and float(ya) > 0 and float(yb) > 0
                and float(ya) - float(yb) > EXIT_SPIKE_SPREAD + 1e-9
                and (_our_now is None or _our_now > (float(entry_price) if entry_price else 1.0) * EXIT_INSTANT_FLOOR + 1e-9)):
            _first = _spike_wait.setdefault(ticker, time.time())
            if time.time() - _first < EXIT_SPIKE_WAIT_SECONDS:
                print(f"SPIKE GUARD: {ticker} -- book torn open (YES bid ${float(yb):.2f} / ask ${float(ya):.2f}); "
                      f"holding the sell {time.time() - _first:.1f}s of {EXIT_SPIKE_WAIT_SECONDS}s for buyers to come back.")
                return {"_sold": 0.0, "waiting": True}
            print(f"SPIKE GUARD: {ticker} -- still torn open after {EXIT_SPIKE_WAIT_SECONDS}s -- selling now.")
        else:
            _spike_wait.pop(ticker, None)
        if close_side == "bid" and ya is not None and float(ya) > 0:
            price = min(0.99, round(float(ya) + _slip, 2))
        elif close_side == "ask" and yb is not None and float(yb) > 0:
            price = max(0.01, round(float(yb) - _slip, 2))
    except Exception as e:
        print(f"EXIT PRICE: couldn't read the live quote for {ticker} ({e}) -- using the wide limit this once.")
    if close_side == "bid":
        real_side_desc = f"closing to YES @ up to ${price:.2f} (immediate-or-cancel, max 10c past the quote)"
    else:
        real_side_desc = f"closing to NO @ down to ${1 - price:.2f} (raw order: sell YES @ ${price:.2f}, immediate-or-cancel, max 10c past the quote)"
    print(f"{'EXIT DRY-RUN' if dry_run else 'EXITING'} {close_side.upper()} {ticker} x{count} -- {real_side_desc}")
    if dry_run:
        return {"dry_run": True}
    # EXIT GUARD (17 Oct, BTC 06:00 ET): the hard backstop and the normal stop-loss both fired on the same crash a few
    # seconds apart, and Kalshi's position list lagged, so the EXIT SAFETY check below still saw the old position and the
    # second sell opened a brand-new opposite position (held=-15). One exit per ticker per 30s.
    _last = _exit_sent_at.get(ticker)
    if _last is not None and time.time() - _last < EXIT_IN_FLIGHT_SECONDS:
        print(f"EXIT GUARD: {ticker} -- an exit order was already sent {time.time() - _last:.0f}s ago -- "
              f"skipping this duplicate so it can't open a new opposite position.")
        return {"skipped": True, "reason": "exit_in_flight"}
    # EXIT SAFETY CHECK: an exit order that finds nothing to close does not do nothing -- it OPENS a new opposite
    # position (a bid up to 0.99 simply buys YES). That happened when two copies of the bot (overlapping deploys)
    # both exited the same trade. So before exiting, ask Kalshi what is really held, sell only that, and skip the
    # exit if the position is already gone or points the other way. If the lookup itself fails, exit anyway --
    # protecting against a loss matters more than the (rare) risk of a duplicate.
    closable = float(count)
    try:
        positions_resp = client.get_positions()
        real_positions = positions_resp.get("market_positions", positions_resp.get("positions", []))
        held = 0.0
        for p in real_positions:
            if p.get("ticker") == ticker:
                held = float(p.get("position_fp", p.get("position", 0)) or 0)
                break
        closable = held if close_side == "ask" else -held   # ask closes a long YES position, bid closes a short
        if closable < 1e-9:
            print(f"EXIT SAFETY: {ticker} -- Kalshi shows no {'YES' if close_side == 'ask' else 'NO'} position left to close "
                  f"(held={held}) -- skipping the exit so it can't open a new opposite position.")
            return {"skipped": True, "reason": "nothing_to_close"}
        if float(count) > closable + 1e-9:
            print(f"EXIT SAFETY: {ticker} -- trying to close {count} but Kalshi only shows {closable:g} held -- "
                  f"closing {closable:g} instead.")
            count = closable
    except Exception as e:
        print(f"EXIT SAFETY: couldn't verify the real position for {ticker} ({e}) -- exiting anyway.")
    _bal_before = None
    try:
        _bal_before = float(client.get_balance().get("balance")) / 100
    except Exception:
        pass
    resp = client.create_order(
        ticker=ticker, client_order_id=f"pulse-x-{uuid.uuid4()}",
        side=close_side, count=str(count), price=f"{price:.2f}", time_in_force="immediate_or_cancel",
    )
    _exit_sent_at[ticker] = time.time()   # only after Kalshi accepted it, so a failed order can still be retried
    # Lesson from DOGE 19 Oct 21:14 CT: the immediate-or-cancel exit at 50c didn't fill (the price was falling fast),
    # and the 30s duplicate guard then blocked every retry -- NO slid from 62c to 36c before the next try went out.
    # Now: check what actually filled. Nothing filled -> allow a fresh try (at the new price) in 2s. Something filled
    # -> wait 8s so a lagging position list can't make us sell twice and open the opposite side.
    try:
        before = closable
        time.sleep(0.6)
        positions_resp = client.get_positions()
        held_after = 0.0
        for p in positions_resp.get("market_positions", positions_resp.get("positions", [])):
            if p.get("ticker") == ticker:
                held_after = float(p.get("position_fp", p.get("position", 0)) or 0)
                break
        left = held_after if close_side == "ask" else -held_after
        if left >= before - 1e-9:
            _exit_misses[ticker] = _exit_misses.get(ticker, 0) + 1
            if isinstance(resp, dict):
                resp["_sold"] = 0.0
            print(f"EXIT: {ticker} -- nothing filled at ${price:.2f}; retrying in 1s with a wider limit (try {_exit_misses[ticker] + 1}).")
            _exit_sent_at[ticker] = time.time() - (EXIT_IN_FLIGHT_SECONDS - 1)
        else:
            sold = before - max(left, 0)
            _exit_misses.pop(ticker, None)
            _spike_wait.pop(ticker, None)
            if isinstance(resp, dict):
                resp["_sold"] = sold
            print(f"EXIT: {ticker} -- sold {sold:g} of {before:g}; {max(left, 0):g} left.")
            _exit_sent_at[ticker] = time.time() - (EXIT_IN_FLIGHT_SECONDS - 8)
            # 20 Oct: record what the sale REALLY paid (cash in / shares sold), not the screen price when the stop fired.
            # The old logs used the quote at the moment of the decision, which hid how badly fast exits really filled.
            try:
                if _bal_before is not None and sold > 0:
                    _bal_after = float(client.get_balance().get("balance")) / 100
                    real_px = (_bal_after - _bal_before) / sold
                    if 0.0 <= real_px <= 1.0:
                        print(f"EXIT REAL FILL: {ticker} -- {sold:g} sold for ${_bal_after - _bal_before:.2f} total "
                              f"(${real_px:.2f}/share after fees).")
                        if isinstance(resp, dict):
                            resp["_real_exit_px"] = real_px
            except Exception as e:
                print(f"EXIT REAL FILL: couldn't read the balance after selling {ticker} ({e}).")
    except Exception as e:
        print(f"EXIT: couldn't re-check {ticker} after the order ({e}) -- keeping the normal guard.")
    return resp


def get_shares_available(client: KalshiClient, ticker: str, side: str):
    try:
        resp = client.get_orderbook(ticker)
        if "orderbook" not in resp:
            return None
        book = resp["orderbook"]
        side_key = "yes" if side == "bid" else "no"
        if side_key not in book:
            return None
        levels = book[side_key]
        if not levels:
            return 0
        return sum(level[1] for level in levels if len(level) >= 2)
    except Exception:
        return None


_permanently_unexitable_tickers = set()


def evaluate_any_market_for_exit(market: dict):
    try:
        yes_ask = market.get("yes_ask_dollars")
        yes_bid = market.get("yes_bid_dollars")
        if yes_ask is None or yes_bid is None:
            return None
        market_price = (float(yes_ask) + float(yes_bid)) / 2
        close_time_str = market.get("close_time")
        seconds_remaining = 0.0
        if close_time_str:
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            seconds_remaining = (close_time - datetime.now(timezone.utc)).total_seconds()
        return SimpleNamespace(
            ticker=market.get("ticker", ""),
            title=market.get("title", market.get("ticker", "")),
            market_price=market_price,
            seconds_remaining=seconds_remaining,
        )
    except (TypeError, ValueError) as e:
        print(f"  evaluate_any_market_for_exit couldn't parse {market.get('ticker', '?')} ({e}) "
              f"-- skipping this ticker's exit check this cycle rather than crashing the whole bot.")
        return None


def current_position_value_for_log(position: dict, market_price: float) -> float:
    return market_price if position["side"] == "bid" else (1 - market_price)


_floor_check_cache = [None, 0.0]
_stop_loss_count = [0]
FLOOR_CHECK_CACHE_SECONDS = 2
_generic_series_cache = [None, 0.0]
GENERIC_SERIES_CACHE_SECONDS = 300


def get_cached_generic_short_markets(client: KalshiClient) -> dict:
    if _generic_series_cache[0] is not None and (time.time() - _generic_series_cache[1]) < GENERIC_SERIES_CACHE_SECONDS:
        return _generic_series_cache[0]
    found = discover_generic_short_markets(client)
    _generic_series_cache[0] = found
    _generic_series_cache[1] = time.time()
    return found


def get_fresh_balance_for_floor_check(client: KalshiClient, fallback_bankroll: float) -> float:
    if _floor_check_cache[0] is not None and (time.time() - _floor_check_cache[1]) < FLOOR_CHECK_CACHE_SECONDS:
        return _floor_check_cache[0]
    try:
        real_balance = client.get_balance()
        if "balance" in real_balance:
            fresh = real_balance["balance"] / 100
            _floor_check_cache[0] = fresh
            _floor_check_cache[1] = time.time()
            return fresh
    except Exception:
        pass
    return fallback_bankroll


def ensure_shard_funds(client: KalshiClient, ticker: str, needed_dollars: float) -> None:
    """Kalshi keeps cash per exchange shard (crypto/commodities live on one shard). If the market's shard
    holds less than this trade needs, move the shortfall in from the shard with the most cash."""
    try:
        market = client.get_market(ticker).get("market", {})
        shard = market.get("exchange_index")
        if shard is None:
            return
        shard = int(shard)
        balances = client.get_balance_by_shard()
        have = balances.get(shard, 0.0)
        need = needed_dollars + 0.05
        if have + 1e-9 >= need:
            return
        shortfall = round(need - have + 0.50, 2)   # small cushion so the next trade doesn't need another transfer
        donors = sorted(((v, i) for i, v in balances.items() if i != shard), reverse=True)
        if not donors or donors[0][0] < 0.01:
            print(f"  SHARD FUNDS: shard {shard} has ${have:.2f}, needs ${need:.2f}, and no other shard has cash.")
            return
        donor_balance, donor = donors[0]
        amount = min(shortfall, donor_balance)
        client.transfer_between_shards(donor, shard, amount)
        print(f"  SHARD FUNDS: moved ${amount:.2f} from shard {donor} to shard {shard} "
              f"(shard {shard} had ${have:.2f}, trade needs ${needed_dollars:.2f}).")
        _floor_check_cache[0] = None
        time.sleep(1.0)
    except Exception as e:
        # Used to say "placing the order anyway" -- but with the shard under-funded that just sent an order Kalshi
        # rejects with insufficient_balance (and burned one of the ticker's attempts). Fail closed: callers skip
        # this pass and try again on the next scan.
        print(f"  SHARD FUNDS: couldn't confirm/move funds ({e}) -- NOT placing this order.")
        raise


def decide_entry_side_and_price(seconds_remaining: float, market_price: float):
    return None


_market_cache = {}          # ticker -> (market dict, fetched_at)
_MARKET_CACHE_TTL = 1.0     # seconds: one stop-loss pass reuses a single price lookup per market


def get_market_cached(client: KalshiClient, ticker: str) -> dict:
    """Market data for the stop-loss path. Settlement, stop-loss and backstop checks used to each fetch the same market
    separately (3 lookups per open position per pass); now they share one if it is under a second old."""
    cached = _market_cache.get(ticker)
    if cached and (time.time() - cached[1]) < _MARKET_CACHE_TTL:
        return cached[0]
    market = client.get_market(ticker).get("market", {})
    _market_cache[ticker] = (market, time.time())
    if len(_market_cache) > 500:
        for k in [k for k, v in _market_cache.items() if time.time() - v[1] > 60]:
            _market_cache.pop(k, None)
    return market


def prefetch_markets(client: KalshiClient, tickers: list) -> None:
    """Fetch several markets at the same time (instead of one after another) and fill the cache."""
    tickers = [t for t in tickers if t]
    if len(tickers) < 2:
        return
    from concurrent.futures import ThreadPoolExecutor

    def _one(t):
        try:
            _market_cache[t] = (client.get_market(t).get("market", {}), time.time())
        except Exception:
            pass   # the normal per-check fetch will retry and log the error itself

    with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as pool:
        list(pool.map(_one, tickers))


HARD_BACKSTOP_FRACTION = 0.72                # was 0.78 -- kept below the 80% stop so it doesn't fire first
HARD_BACKSTOP_CRASH_FRACTION = 0.50   # flip/ride positions: below this, sell instantly no matter what
HARD_BACKSTOP_CONFIRM_SECONDS = 10    # flip/ride positions: otherwise the drop must hold this long (and win < 90%)
_backstop_since = {}


def check_hard_backstop(client: KalshiClient, state: dict) -> list:
    triggered_pnls = []
    try:
        positions_resp = client.get_positions()
    except Exception as e:
        print(f"HARD BACKSTOP: couldn't fetch real positions ({e}) -- skipping this cycle's check.")
        return triggered_pnls
    real_positions = positions_resp.get("market_positions", positions_resp.get("positions", []))
    for p in real_positions:
        try:
            ticker = p.get("ticker")
            if not ticker:
                continue
            if ticker in _permanently_unexitable_tickers or ticker in markets_paper.owned():
                continue
            position_fp = float(p.get("position_fp", p.get("position", 0)) or 0)
            if position_fp == 0:
                continue
            exposure = float(p.get("market_exposure_dollars", 0) or 0)
            count = abs(position_fp)
            if count == 0 or exposure <= 0:
                continue
            real_entry_price = exposure / count
            if not (0.01 <= real_entry_price <= 1.00):
                print(f"HARD BACKSTOP: {ticker} derived entry price (${real_entry_price:.4f}) looks "
                      f"unreasonable -- skipping rather than risk acting on bad data.")
                continue
            side = "bid" if position_fp > 0 else "ask"
            market = get_market_cached(client, ticker)
            if market.get("status") == "finalized":
                continue
            yes_ask = market.get("yes_ask_dollars")
            yes_bid = market.get("yes_bid_dollars")
            if yes_ask is None or yes_bid is None:
                continue
            market_price = (float(yes_ask) + float(yes_bid)) / 2
            current_value = market_price if side == "bid" else (1 - market_price)
            _pos0 = state.get(ticker) or {}
            if not _pos0.get("ride") and flip_star.FLIP_STOPS_ONLY_6040_AND_CRASH:
                # Pulse bets: backstop only fires on the 60/40 line or a 50% crash (same as the main exit loop).
                if not (current_value <= real_entry_price * STOP_FRACTION_OF_ENTRY + 1e-9
                        or current_value * count <= real_entry_price * count * flip_star.FLIP_CRASH_FRACTION + 1e-9):
                    _backstop_since.pop(ticker, None)
                    continue
            _sl = _secs_left_from_market(market)
            if _sl is not None and _sl <= LAST_WINDOW_SECONDS:
                if current_value > real_entry_price * STOP_FRACTION_OF_ENTRY + 1e-9:
                    _backstop_since.pop(ticker, None)
                    continue   # last 30s: only the 60/40 pull-out applies (handled in the main exit loop)
            real_bet_dollars = real_entry_price * count
            real_current_dollars = current_value * count
            threshold_dollars = real_bet_dollars * HARD_BACKSTOP_FRACTION
            if real_current_dollars > threshold_dollars + 1e-9:
                _backstop_since.pop(ticker, None)
                continue
            # Lesson from NEAR 15 Oct 20:09 CT: a 4-share flip-star NO bought at 94c; 20s later one thin-book spike
            # (YES 7c -> 29c) dragged the mid-price under 78% while the model still gave us 93%. The backstop sold
            # instantly at ~71c for about -$1, and NO was back to 92c a minute later.
            # For flip-star / ride positions the backstop now waits for the drop to hold and the win % to agree,
            # exactly like the primary stop. A real crash (value under half the bet, or win under 75%) still sells at once.
            pos = state.get(ticker) or {}
            if pos.get("flip_star") or pos.get("ride"):
                win = flip_star.win_now(ticker)
                crash = real_current_dollars <= real_bet_dollars * HARD_BACKSTOP_CRASH_FRACTION
                collapse = win is not None and win < flip_star.FLIP_EXIT_PANIC_WIN
                if not (crash or collapse):
                    since = _backstop_since.setdefault(ticker, time.time())
                    held = time.time() - since
                    if held < HARD_BACKSTOP_CONFIRM_SECONDS or (win is not None and win >= 0.90):
                        print(f"HARD BACKSTOP: {ticker} value ${real_current_dollars:.2f} under {HARD_BACKSTOP_FRACTION*100:.0f}% of ${real_bet_dollars:.2f} "
                              f"for {held:.0f}s, win {('?' if win is None else f'{win*100:.0f}%')} -- waiting "
                              f"(sells if it holds {HARD_BACKSTOP_CONFIRM_SECONDS}s with win under 90%, or at once on a crash).")
                        continue
            _backstop_since.pop(ticker, None)
            print(f"HARD BACKSTOP TRIGGERED: {ticker} real value ${real_current_dollars:.2f} fell to/below "
                  f"{HARD_BACKSTOP_FRACTION*100:.0f}% of real bet ${real_bet_dollars:.2f} "
                  f"(threshold ${threshold_dollars:.2f}) -- forcing an emergency exit, independent of "
                  f"the primary stop-loss system.")
            close_side = "ask" if side == "bid" else "bid"
            try:
                exit_resp = place_exit(client, ticker, close_side, count, dry_run=False, entry_price=real_entry_price)
                if isinstance(exit_resp, dict) and exit_resp.get("waiting"):
                    continue   # spike guard is holding this sell for a few seconds
                if isinstance(exit_resp, dict) and exit_resp.get("skipped"):
                    if ticker in state:
                        del state[ticker]
                    continue
            except Exception as e:
                if "market_closed" in str(e) or "market_not_found" in str(e):
                    _permanently_unexitable_tickers.add(ticker)
                    print(f"HARD BACKSTOP: {ticker} exit can't ever succeed ({e}) -- market is already "
                          f"closed. Marked permanently unexitable; check_settlements() will pick up the "
                          f"real result once Kalshi finishes settling it. No further retries here.")
                else:
                    print(f"HARD BACKSTOP: emergency exit order for {ticker} failed ({e}) -- will retry next cycle.")
                continue
            pnl = (current_value - real_entry_price) * count
            triggered_pnls.append(pnl)
            flip_star.record_result(state.get(ticker), pnl, "emergency backstop")
            if ticker in state:
                del state[ticker]
        except Exception as e:
            print(f"HARD BACKSTOP: error checking {p.get('ticker', '?')} ({e}) -- skipping this one, "
                  f"continuing to check other real positions.")
            continue
    return triggered_pnls


BOT_ORDER_ID_PREFIX = "pulse-"
PULSE_SERIES = {f"KX{c}15M" for c in ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE", "ZEC", "NEAR", "BCH", "SUI")}
PROCESS_START_TIME = time.time()


def placed_by_pulse(client: KalshiClient, ticker: str):
    """True if Kalshi's order history for this market contains an order this bot placed (its client_order_id starts with
    'pulse-'), False if it has orders but none are ours (i.e. placed by hand), None if the lookup failed or found nothing."""
    try:
        resp = client._request("GET", "/portfolio/orders", params={"ticker": ticker, "limit": 100})
        orders = resp.get("orders", []) or []
        if not orders:
            return None
        # 25 Oct 18:35 CT: only an ENTRY order counts as "Pulse placed it". Pulse's own SELL orders ("pulse-x-") used to
        # count too, so after Pulse partly sold a manual bet the rest was re-adopted as "bot-placed" and got Pulse's stops
        # (SOL manual bet 25 Oct 18:22 sold by the 80c line). Every Pulse entry since Oct 15 is tagged, so a market with
        # orders but no tagged entry was placed by hand.
        def _is_entry(o):
            c = str(o.get("client_order_id", ""))
            return c.startswith(BOT_ORDER_ID_PREFIX) and not c.startswith(BOT_ORDER_ID_PREFIX + "x-")
        if any(_is_entry(o) for o in orders):
            return True
        return False
    except Exception as e:
        print(f"  Couldn't read order history for {ticker} ({e}) -- treating its origin as unknown.")
        return None


def actual_fill_cost(client: KalshiClient, ticker: str, tries: int = 4, delay: float = 0.4):
    """Real average cost per contract of the position Kalshi shows for this ticker (exposure / count), or None."""
    for _ in range(tries):
        try:
            resp = client.get_positions()
            for p in resp.get("market_positions", resp.get("positions", [])):
                if p.get("ticker") != ticker:
                    continue
                fp = abs(float(p.get("position_fp", p.get("position", 0)) or 0))
                exp = float(p.get("market_exposure_dollars", 0) or 0)
                if fp > 0 and exp > 0 and 0.01 <= exp / fp <= 1.0:
                    return exp / fp
        except Exception:
            pass
        time.sleep(delay)
    return None


_settled_tickers = set()   # tickers check_settlements already booked this run


def adopt_manual_positions(client: KalshiClient, state: dict) -> None:
    try:
        positions_resp = client.get_positions()
    except Exception as e:
        print(f"Couldn't check for manual positions to adopt ({e}) -- skipping this cycle's check.")
        return
    real_positions = positions_resp.get("market_positions", positions_resp.get("positions", []))
    for p in real_positions:
        ticker = p.get("ticker")
        if ticker and ticker in state and ticker in markets_paper.owned():
            state.pop(ticker, None)   # the commodity/currency bot owns this one -- Pulse lets it go
            continue
        if ticker and ticker not in state:
            try:
                _m = get_market_cached(client, ticker)
                if _m.get("result"):
                    _settled_tickers.add(ticker)   # market already decided (survives redeploys too)
            except Exception:
                pass
        if ticker in _settled_tickers:
            continue   # 26 Oct: already settled -- Kalshi still lists it until payout; don't re-adopt & re-count it
        if not ticker or ticker in state or ticker in flip_star.pending_tickers() or ticker in markets_paper.owned():
            continue   # (a resting flip-star bid that just filled is flip_star's to record, not a manual bet)
        position_fp = float(p.get("position_fp", p.get("position", 0)) or 0)
        if position_fp == 0:
            continue
        exposure = float(p.get("market_exposure_dollars", 0) or 0)
        count = abs(position_fp)
        if count == 0:
            continue
        entry_price = exposure / count
        if not (0.01 <= entry_price <= 1.00):
            print(f"  Skipping adoption of {ticker} -- derived entry price (${entry_price:.4f}) "
                  f"looks unreasonable, not adopting rather than risk a broken stop-loss. "
                  f"RAW FIELDS: position_fp={p.get('position_fp')!r}, position={p.get('position')!r}, "
                  f"market_exposure_dollars={p.get('market_exposure_dollars')!r}, "
                  f"computed count={count}, computed exposure={exposure}")
            continue
        side = "bid" if position_fp > 0 else "ask"
        # Who placed it? Only a position with NO order from this bot in its history is treated as manual (85%-of-cost stop only).
        # A bot-placed position that was merely forgotten across a restart keeps the normal stop; if the origin can't be
        # determined, it also gets the normal stop (safer than selling something the bot itself bought).
        by_bot = placed_by_pulse(client, ticker)
        # 25 Oct 18:35 CT: Pulse only ever bets on the crypto 15-minute coins. Anything else (natural gas etc.) is manual.
        _series = ticker.split("-")[0]
        if by_bot is not True and _series not in PULSE_SERIES:
            by_bot = False
        is_manual = (by_bot is False)
        state[ticker] = {"count": count, "entry_price": entry_price, "side": side,
                          "dry_run": False, "peak_gain_per_contract": 0.0,
                          "entry_time": time.time(), "manually_adopted": is_manual, "adopted": True,
                          # 20 Oct 00:20 CT: a Pulse bet re-adopted after a restart/redeploy keeps the flip-star rules
                          # (red <0.2 sigma, 70/30 hard stop, 60/40 last window) instead of falling back to the 80c stop.
                          # SOL & ETH NO were sold at 79c/76c at 00:10 this way right after a redeploy, then came back.
                          "flip_star": not is_manual}
        origin = ("MANUAL (no Pulse order in its history) -- 85%-of-cost stop only" if is_manual else
                  "bot-placed" if by_bot else "origin unknown -- normal stop")
        print(f"  ADOPTED {'manual' if is_manual else 'existing'} position: {ticker} {side.upper()} x{count:.0f} @ ~${entry_price:.2f} "
              f"(derived from Kalshi's real position data; {origin}) -- stop-loss now protecting it.")


STATE_LOCK = threading.Lock()
EXIT_CHECK_POLL_SECONDS = 0.5


def check_exits(client: KalshiClient, state: dict, excluded_tickers: set) -> list:
    settlements = load_json(SETTLEMENTS_FILE, [])
    realized_pnl = []
    stop_loss_occurred_this_cycle = False
    for ticker in list(state.keys()):
        if ticker in _permanently_unexitable_tickers:
            continue
        position = state[ticker]
        try:
            try:
                market = get_market_cached(client, ticker)
            except Exception as e:
                # ADDED, per real evidence of a position that silently
                # never got its stop-loss checked -- this used to fail
                # with zero logging, making it indistinguishable from
                # "working correctly, just not triggered yet."
                print(f"  STOP-LOSS CHECK: couldn't fetch market data for {ticker} ({e}) -- "
                      f"skipping this cycle, will retry next cycle.")
                continue
            if market.get("status") == "finalized":
                continue
            result = evaluate_crypto_market(market)
            if result is None:
                result = evaluate_commodity_market(market)
            if result is None:
                result = evaluate_any_market_for_exit(market)
            if result is None:
                # ADDED, per real evidence -- same reasoning as above.
                # If a market's real data shape doesn't match any of
                # the three evaluators (e.g. a sports market with a
                # different field layout than crypto/commodity
                # markets), this position NEVER reaches the stop-loss
                # math at all, forever, with no indication why.
                print(f"  STOP-LOSS CHECK: {ticker} couldn't be evaluated by any of the three "
                      f"evaluators (crypto/commodity/generic) -- this position's real data shape "
                      f"doesn't match what any of them expect. RAW MARKET FIELDS: {list(market.keys())}. "
                      f"This stop-loss is NOT being checked until this is fixed.")
                continue
            reversion_signal = reversion.record_and_score(ticker, result.market_price)
            current_value = current_position_value(position, result.market_price)
            current_gain = current_value - position["entry_price"]
            position["peak_gain_per_contract"] = max(position.get("peak_gain_per_contract", 0.0), current_gain)
            flip_dec = (flip_star.exit_decision(ticker) if position.get("flip_star") and not flip_star.FLIP_STOPS_ONLY_6040_AND_CRASH else None)
            if position.get("ride"):
                # 1-share ride: trailing take-profit / turned / stop, all in ride.py (hard backstop still applies).
                decision = ExitDecision(*ride.exit_decision(ticker, position, market, result.seconds_remaining))
            elif flip_dec is not None:
                # Flip-star positions: hold through yellow, pull out as soon as "To flip" turns red on the board.
                decision = ExitDecision(*flip_dec)
                if not decision.should_exit:
                    # ...and they also get the universal stop-loss (80% of what was paid / held side 80c), whichever comes first.
                    _std = check_exit(position, result.market_price, getattr(result, "model_prob", 0.5), result.seconds_remaining,
                                      reversion_z=reversion_signal.z_score, partial_profit_fraction=None,
                                      peak_gain_per_contract=position["peak_gain_per_contract"])
                    if _std.should_exit and (_std.reason.startswith("fill_outside") or _std.reason.startswith("crash_stop") or _std.reason.startswith("hard_stop")):
                        decision = _std
                    elif _std.should_exit and _std.reason.startswith("stop_loss") and flip_star.FLIP_USE_80_STOP:
                        # Lesson from XRP 15 Oct 22:43: with ~70s left the book thinned out, the mid price blipped to 78c for a
                        # moment and the 84% stop sold a position the model still gave 95% (the price was back at 98c seconds
                        # later). For flip-star trades the price stop now has to HOLD and the model has to agree.
                        _since = position.setdefault("stop_since", time.time())
                        _win = flip_star.win_now(ticker)
                        _held = time.time() - _since
                        if _held >= flip_star.FLIP_STOP_CONFIRM_SECONDS and (_win is None or _win < flip_star.FLIP_EXIT_MAX_WIN):
                            decision = ExitDecision(True, _std.reason + f" [held {_held:.0f}s, win {'?' if _win is None else f'{_win*100:.0f}%'}]")
                        elif _held < 1.0:
                            print(f"[FLIP] {ticker}: price under the 80% stop (win {'?' if _win is None else f'{_win*100:.0f}%'}) -- "
                                  f"waiting {flip_star.FLIP_STOP_CONFIRM_SECONDS}s for it to hold before selling.")
                    else:
                        position.pop("stop_since", None)
            else:
                # Everything else -- and flip-star positions while the board's numbers are unavailable -- uses the normal stop.
                # (Flip-star positions without board numbers only take the hard 70/30, crash and bad-fill stops, not the 80c one.)
                decision = check_exit(position, result.market_price, getattr(result, "model_prob", 0.5), result.seconds_remaining,
                                       reversion_z=reversion_signal.z_score, partial_profit_fraction=None,
                                       peak_gain_per_contract=position["peak_gain_per_contract"])
                if (position.get("flip_star") and decision.should_exit and not flip_star.FLIP_USE_80_STOP
                        and decision.reason.startswith("stop_loss")):
                    decision = ExitDecision(False, "")
            if not position.get("ride") and flip_star.FLIP_STOPS_ONLY_6040_AND_CRASH:
                # 20 Oct 11:31: applies to manual bets too (BTC manual NO @92c was sold by the 70c stop at 11:27)
                # Only two stops for Pulse bets: 60/40 (held side <= 60c) and the 50% crash stop. Everything else is ignored.
                _hv = current_position_value(position, result.market_price)
                _zr = _held_sigma_now(ticker, position)
                if (_zr is None or _zr > flip_star.FLIP_EXIT_SIGMA + 1e-9) and position.pop("red_since", None):
                    print(f"{ticker}: back out of red (To flip {('?' if _zr is None else f'{_zr:.2f}')} sigma) -- holding.")
                if position.get("force_exit"):
                    pass   # bad fill outside the allowed price range -- keep the immediate exit
                elif position.get("manually_adopted"):
                    # 25 Oct 13:49 CT (Matthew): manual bets get ONLY their 85%-of-cost stop. The absolute 75c line was
                    # selling manual bets bought under 75c the second they were adopted (BTC manual @73c sold at 74c).
                    if _hv <= position["entry_price"] * flip_star.MANUAL_STOP_FRACTION + 1e-9:
                        decision = ExitDecision(True, f"manual_stop (held side {_hv*100:.0f}c <= {flip_star.MANUAL_STOP_FRACTION*100:.0f}% "
                                                      f"of entry ${position['entry_price']:.2f})")
                    else:
                        decision = ExitDecision(False, "")
                elif _hv <= position["entry_price"] * STOP_FRACTION_OF_ENTRY + 1e-9:
                    # 25 Oct 13:27 CT (Matthew): at/under 75c -> sell INSTANTLY, any time in the round.
                    position.pop("crash_since", None)
                    decision = ExitDecision(True, f"stop_80pct (held side {_hv*100:.0f}c <= 80% of entry ${position['entry_price']:.2f} -- instant)")
                elif (position.get("flip_star") and _held_sigma_now(ticker, position) is not None
                        and _held_sigma_now(ticker, position) <= flip_star.FLIP_EXIT_SIGMA + 1e-9):
                    _zs_held = _held_sigma_now(ticker, position)
                    # 26 Oct 17:30 CT (Matthew): sell once To flip for our side has been red (<= 0.35 sigma) for 3 seconds
                    # -- same rule as Pulse Markets. (Replaces "instant, only if held side <= 90c".)
                    _rs = position.setdefault("red_since", time.time())
                    if time.time() - _rs >= RED_EXIT_SECONDS:
                        decision = ExitDecision(True, f"sigma_exit (To flip {_zs_held:.2f} sigma <= {flip_star.FLIP_EXIT_SIGMA:g} "
                                                      f"for {time.time()-_rs:.1f}s, held side {_hv*100:.0f}c)")
                    else:
                        decision = ExitDecision(False, "")
                elif PULSE_PCT_STOPS_ENABLED and _hv <= position["entry_price"] * flip_star.FLIP_CRASH_FRACTION + 1e-9:
                    # 25 Oct 13:27 CT (Matthew): the 87%-of-entry stop must hold CRASH_CONFIRM_SECONDS before selling.
                    _cs = position.setdefault("crash_since", time.time())
                    if time.time() - _cs >= CRASH_CONFIRM_SECONDS:
                        decision = ExitDecision(True, f"crash_stop (held side {_hv*100:.0f}c <= {flip_star.FLIP_CRASH_FRACTION*100:.0f}% of entry "
                                                      f"${position['entry_price']:.2f} for {time.time()-_cs:.1f}s)")
                    else:
                        print(f"CRASH STOP: {ticker} held side {_hv*100:.0f}c under {flip_star.FLIP_CRASH_FRACTION*100:.0f}% of entry for "
                              f"{time.time()-_cs:.1f}s -- waiting {CRASH_CONFIRM_SECONDS}s before selling.")
                        decision = ExitDecision(False, "")
                elif position.get("manually_adopted") and _hv <= position["entry_price"] * flip_star.MANUAL_STOP_FRACTION + 1e-9:
                    # 24 Oct 13:53 CT (Matthew): manual bets get an instant 85%-of-cost stop.
                    decision = ExitDecision(True, f"manual_stop (held side {_hv*100:.0f}c <= {flip_star.MANUAL_STOP_FRACTION*100:.0f}% "
                                                  f"of entry ${position['entry_price']:.2f})")
                else:
                    position.pop("line_since", None)
                    position.pop("crash_since", None)
                    decision = ExitDecision(False, "")
            _secs = getattr(result, "seconds_remaining", None)
            if _secs is not None and _secs <= LAST_WINDOW_SECONDS and not position.get("manually_adopted"):
                _held_val = current_position_value(position, result.market_price)
                if _held_val <= position["entry_price"] * HARD_BACKSTOP_CRASH_FRACTION + 1e-9:
                    decision = ExitDecision(True, f"crash_stop ({_held_val*100:.0f}c held side <= 50% of entry with {_secs:.0f}s left -- instant)")
                elif _held_val <= position["entry_price"] * STOP_FRACTION_OF_ENTRY + 1e-9:
                    _held_for = _line_held_seconds(position)
                    if _held_for >= LINE_STOP_CONFIRM_SECONDS:
                        decision = ExitDecision(True, f"last_window_stop ({_held_val*100:.0f}c held side <= 80% of entry "
                                                      f"for {_held_for:.1f}s with {_secs:.0f}s left -- selling)")
                    else:
                        print(f"STOP LINE (last {LAST_WINDOW_SECONDS}s): {ticker} held side {_held_val*100:.0f}c for {_held_for:.1f}s -- "
                              f"waiting {LINE_STOP_CONFIRM_SECONDS}s for it to hold before selling.")
                        decision = ExitDecision(False, "")
                elif decision.should_exit and decision.reason.startswith("manual_stop"):
                    pass   # manual bets keep their 85% stop right through the last 45s
                elif decision.should_exit and decision.reason.startswith("sigma_exit"):
                    pass   # 25 Oct 13:36 CT (Matthew): the 0.35-sigma exit applies all the time, last 45s included
                else:
                    position.pop("line_since", None)
                    if decision.should_exit:
                        print(f"LAST {LAST_WINDOW_SECONDS}s: {ticker} -- ignoring '{decision.reason[:40]}' (held side "
                              f"{_held_val*100:.0f}c, above the 80%-of-entry line {position['entry_price']*STOP_FRACTION_OF_ENTRY*100:.0f}c) -- holding to settle.")
                    decision = ExitDecision(False, "")
            # INSTANT STOP (86% of entry): applied last so no line-stop hold, last-45s rule or stop filter above can
            # block it. No hold/confirm. If another exit already fired this cycle, that one is kept (single sell).
            if not decision.should_exit:
                # 25 Oct 13:27 CT (Matthew): the 86% stop also waits CRASH_CONFIRM_SECONDS (same timer as the 87% stop).
                _isd = (ExitDecision(False, "") if (position.get("manually_adopted") or not PULSE_PCT_STOPS_ENABLED)
                        else instant_stop_decision(position, result.market_price))
                if _isd.should_exit:
                    _cs = position.setdefault("crash_since", time.time())
                    if time.time() - _cs >= CRASH_CONFIRM_SECONDS:
                        decision = _isd
                elif position.get("ride") or not flip_star.FLIP_STOPS_ONLY_6040_AND_CRASH:
                    position.pop("crash_since", None)   # Pulse bets reset this timer in the stop block above
            # PROFIT LOCK (21 Oct 13:26 CT, Matthew): once a Pulse bet has been up $0.25 or more, it may not turn into a
            # loss -- if it falls back to what was paid, sell at once (any time in the round, last 45s included).
            if (PROFIT_LOCK_ENABLED and not decision.should_exit and position.get("flip_star")
                    and position.get("peak_gain_per_contract", 0.0) * float(position.get("count", 0)) >= PROFIT_LOCK_ARM_DOLLARS - 1e-9):
                _hv2 = current_position_value(position, result.market_price)
                if _hv2 <= position["entry_price"] + 1e-9:
                    decision = ExitDecision(True, f"profit_lock (was up ${position['peak_gain_per_contract']*float(position['count']):.2f}, "
                                                  f"now back to {_hv2*100:.0f}c vs entry {position['entry_price']*100:.0f}c -- selling at break-even)")
            # TAKE PROFIT (25 Oct 13:31 CT, Matthew): cash out as soon as a Pulse bet is up $1.00 or more.
            if not decision.should_exit and position.get("flip_star"):
                _gain_tp = (current_position_value(position, result.market_price) - position["entry_price"]) * float(position.get("count", 0))
                if _gain_tp >= TAKE_PROFIT_DOLLARS - 1e-9:
                    decision = ExitDecision(True, f"take_profit (up ${_gain_tp:.2f} >= ${TAKE_PROFIT_DOLLARS:.2f} -- cashing out)")
            # PROFIT FLOOR (25 Oct 13:29 CT, Matthew): once a Pulse bet has been up $0.50 or more, sell as soon as the gain
            # falls back to $0.25 -- keep at least $0.25 instead of riding it back down.
            if (PROFIT_LOCK_ENABLED and not decision.should_exit and position.get("flip_star")
                    and position.get("peak_gain_per_contract", 0.0) * float(position.get("count", 0)) >= PROFIT_FLOOR_ARM_DOLLARS - 1e-9):
                _hv3 = current_position_value(position, result.market_price)
                _gain_now = (_hv3 - position["entry_price"]) * float(position.get("count", 0))
                if _gain_now <= PROFIT_FLOOR_KEEP_DOLLARS + 1e-9:
                    decision = ExitDecision(True, f"profit_floor (was up ${position['peak_gain_per_contract']*float(position['count']):.2f}, "
                                                  f"now ${_gain_now:.2f} -- cashing out to keep at least ${PROFIT_FLOOR_KEEP_DOLLARS:.2f})")
            if not decision.should_exit:
                continue
            if decision.reason.startswith("instant_stop"):
                print(f"[INSTANT STOP] {ticker} value {current_position_value(position, result.market_price):.2f} <= "
                      f"{INSTANT_STOP_FRACTION*100:.0f}% of entry {position['entry_price']:.2f} -- selling now")
            position_is_real = position.get("dry_run") is False
            send_real_order = position_is_real and not EXIT_DRY_RUN
            close_side = "ask" if position["side"] == "bid" else "bid"
            try:
                exit_resp = place_exit(client, ticker, close_side, position["count"], dry_run=not send_real_order, entry_price=position.get("entry_price"))
                if isinstance(exit_resp, dict) and exit_resp.get("skipped"):
                    # Position was already closed (e.g. by another copy of the bot) -- stop tracking it, record nothing.
                    del state[ticker]
                    excluded_tickers.add(ticker)
                    continue
                # 21 Oct: an exit that filled NOTHING is not an exit. It used to be logged as a loss each try -- the ETH
                # sell at 03:43 CT was counted 3 times and tripped the 3-loss kill switch after 2 real losses. Keep the
                # position tracked and let the next loop retry with a wider limit.
                if isinstance(exit_resp, dict) and exit_resp.get("_sold") is not None and exit_resp["_sold"] <= 1e-9:
                    continue
            except Exception as e:
                if "market_closed" in str(e) or "market_not_found" in str(e):
                    _permanently_unexitable_tickers.add(ticker)
                    print(f"{ticker} exit order can't ever succeed ({e}). Left tracked; waiting for "
                          f"check_settlements() to pick up the real result once Kalshi finishes settling it.")
                else:
                    print(f"Exit order for {ticker} failed ({e}) -- leaving it tracked, will retry next loop.")
                continue
            if position_is_real and EXIT_DRY_RUN:
                print(f"  (real position -- EXIT_DRY_RUN means no real close order was sent)")
                continue
            exit_value = current_position_value_for_log(position, result.market_price)
            if isinstance(exit_resp, dict) and exit_resp.get("_real_exit_px") is not None:
                exit_value = exit_resp["_real_exit_px"]   # what Kalshi really paid, not the screen price
            pnl = (exit_value - position["entry_price"]) * position["count"]
            settlements.append({
                "ticker": ticker, "title": result.title, "result": "early_exit",
                "side": position["side"], "count": position["count"], "entry_price": position["entry_price"],
                "exit_value": round(exit_value, 4), "exit_reason": decision.reason,
                "pnl": round(pnl, 2), "timestamp": time.time(), "dry_run": position.get("dry_run", True),
            })
            realized_pnl.append(pnl)
            print(f"Early exit {ticker}: {decision.reason}, pnl=${pnl:.2f}")
            try:
                pulse_v2.track_live_exit(ticker, position, exit_value)   # follow it to settlement: did the sell help?
            except Exception:
                pass
            flip_star.record_result(position, pnl, "early exit: " + decision.reason[:40])
            sht.record_early_exit(ticker, position["side"], position["entry_price"], position["count"],
                                   exit_value, decision.reason, category="crypto")
            del state[ticker]
            if not decision.reason.startswith("capture_profit"):
                excluded_tickers.add(ticker)
            if decision.reason.startswith("stop_loss") and not position.get("dry_run", True):
                stop_loss_occurred_this_cycle = True
        except Exception as e:
            print(f"STOP-LOSS CHECK FAILED for {ticker} ({e}) -- this position was NOT checked this "
                  f"cycle, but every other open position still was (Layer 1 isolation).")
            continue
    if stop_loss_occurred_this_cycle:
        _stop_loss_count[0] += 1
        print(f"Stop-loss count this session: {_stop_loss_count[0]}")
    if realized_pnl:
        save_json(SETTLEMENTS_FILE, settlements)
    # Circuit-breaker shutdown removed: sys.exit() inside the exit thread only killed that
    # thread, leaving the bot trading with NO stop-loss and NO settlement tracking.
    return realized_pnl


def check_settlements(client: KalshiClient, state: dict) -> list:
    settlements = load_json(SETTLEMENTS_FILE, [])
    newly_settled_pnl = []
    for ticker in list(state.keys()):
        try:
            market = get_market_cached(client, ticker)
        except Exception:
            continue
        status = market.get("status")
        if not market.get("result") or status not in ("finalized", "settled", "determined", "closed"):
            # Stale-position purge: a market that closed >20 min ago and still has no
            # result would otherwise sit in state forever and eat max_open_positions slots.
            try:
                from datetime import datetime, timezone
                ct = market.get("close_time") or market.get("expiration_time")
                if ct and (datetime.now(timezone.utc) - datetime.fromisoformat(ct.replace("Z", "+00:00"))).total_seconds() > 1200:
                    state.pop(ticker, None)
                    print(f"Purged stale position {ticker} (closed >20 min ago, status={status}, no result).")
            except Exception:
                pass
            continue
        position = state.pop(ticker)
        if ticker in _settled_tickers:
            continue   # already booked once
        _settled_tickers.add(ticker)
        won = (position["side"] == "bid" and market["result"] == "yes") or \
              (position["side"] == "ask" and market["result"] == "no")
        pnl = ((1.0 if won else 0.0) - position["entry_price"]) * position["count"]
        settlements.append({
            "ticker": ticker, "title": market.get("title", ticker), "result": market["result"],
            "side": position["side"], "count": position["count"], "entry_price": position["entry_price"],
            "pnl": round(pnl, 2), "timestamp": time.time(),
            "dry_run": position.get("dry_run", True),
        })
        newly_settled_pnl.append(pnl)
        print(f"Settled {ticker}: result={market['result']}, pnl=${pnl:.2f}")
        flip_star.record_result(position, pnl, "settled " + str(market.get("result")))
    if newly_settled_pnl:
        save_json(SETTLEMENTS_FILE, settlements)
    return newly_settled_pnl


def exit_protection_loop(client: KalshiClient, state: dict, excluded_tickers: set,
                          bankroll_and_breaker: dict, stop_event: threading.Event):
    last_adopt = 0.0
    while not stop_event.is_set():
        try:
            with STATE_LOCK:
                # Pick up any position Kalshi shows that we aren't tracking (e.g. one entered by another copy of the bot
                # or before a restart) every ~2 seconds, so the primary stop-loss protects it almost immediately instead
                # of waiting for the next ~15-second scan pass.
                if time.time() - last_adopt >= 2.0:
                    last_adopt = time.time()
                    before = set(state)
                    adopt_manual_positions(client, state)
                    if set(state) != before:
                        save_json(STATE_FILE, state)
                prefetch_markets(client, list(state.keys()))
                settled_pnls = check_settlements(client, state)
                for pnl in settled_pnls:
                    bankroll_and_breaker["bankroll"] += pnl
                    bankroll_and_breaker["breaker"].record_pnl(pnl)
                if settled_pnls:
                    save_json(STATE_FILE, state)

                exit_pnls = check_exits(client, state, excluded_tickers)
                for pnl in exit_pnls:
                    bankroll_and_breaker["bankroll"] += pnl
                    bankroll_and_breaker["breaker"].record_pnl(pnl)
                if exit_pnls:
                    save_json(STATE_FILE, state)

                backstop_pnls = check_hard_backstop(client, state)
                for pnl in backstop_pnls:
                    bankroll_and_breaker["bankroll"] += pnl
                    bankroll_and_breaker["breaker"].record_pnl(pnl)
                if backstop_pnls:
                    save_json(STATE_FILE, state)
        except BaseException as e:
            print(f"EXIT PROTECTION THREAD: unexpected error ({e}) -- thread keeps running, retrying next cycle.")
        stop_event.wait(EXIT_CHECK_POLL_SECONDS)


def run():
    client = KalshiClient()
    state = load_json(STATE_FILE, {})
    # 26 Oct: remove duplicate settlement rows (KXGOLDH was booked ~100x by a re-adopt loop)
    try:
        _rows = load_json(SETTLEMENTS_FILE, [])
        _seen, _clean = set(), []
        for _r in _rows:
            _k = _r.get("ticker")
            if _k in _seen and str(_k).startswith("KXGOLDH-26OCT2616"):
                continue
            _seen.add(_k); _clean.append(_r)
        if len(_clean) != len(_rows):
            save_json(SETTLEMENTS_FILE, _clean)
            print(f"Removed {len(_rows)-len(_clean)} duplicate settlement rows.")
    except Exception as _e:
        print(f"Settlement dedupe skipped ({_e})")
    settlements = load_json(SETTLEMENTS_FILE, [])
    bankroll = STARTING_BANKROLL
    try:
        real_balance = client.get_balance()
        if "balance" in real_balance:
            bankroll = real_balance["balance"] / 100
            print(f"Using real Kalshi balance: ${bankroll:.2f} (STARTING_BANKROLL constant is a fallback only)")
    except Exception as e:
        print(f"Couldn't fetch real balance ({e}) -- falling back to STARTING_BANKROLL=${STARTING_BANKROLL:.2f}")
    breaker = CircuitBreaker(bankroll, RISK_PARAMS)
    breaker.prime_from_settlements(settlements)
    print("=" * 60)
    print("BOT VERSION MARKER: 2026-10-15-FLIP-STAR-v2 + RIDE-v1 (1-share trailing rides)")
    try:
        from config import Config as _C
        _k = _C.API_KEY_ID
        print(f"KALSHI LOGIN CHECK: env={_C.ENV} url={_C.base_url()} key_id_len={len(_k)} key_id_ends=...{_k[-4:]}")
    except Exception as _e:
        print(f"KALSHI LOGIN CHECK: couldn't read config ({_e})")
    print("=" * 60)
    print(f"Bot started. DRY_RUN={DRY_RUN}. EXIT_DRY_RUN={EXIT_DRY_RUN}. Bankroll: ${bankroll:.2f}. "
          f"Today's PnL so far: ${breaker.day_pnl:.2f}. Exit protection running every "
          f"{EXIT_CHECK_POLL_SECONDS}s in its own thread, separate from the {POLL_SECONDS}s market-scan loop.")
    excluded_tickers = set()
    last_balance_sync = time.time()
    BALANCE_SYNC_SECONDS = 300

    bankroll_and_breaker = {"bankroll": bankroll, "breaker": breaker}
    stop_event = threading.Event()
    exit_thread = threading.Thread(
        target=exit_protection_loop,
        args=(client, state, excluded_tickers, bankroll_and_breaker, stop_event),
        daemon=True,
    )
    exit_thread.start()

    while True:
        bankroll = bankroll_and_breaker["bankroll"]
        breaker = bankroll_and_breaker["breaker"]
        with STATE_LOCK:
            adopt_manual_positions(client, state)

        try:
            flip_star.run_entries(client, state, excluded_tickers, STATE_LOCK, DRY_RUN, log_trade, ensure_shard_funds)
        except Exception as e:
            print(f"[FLIP] entry pass failed (non-fatal, other strategies continue): {e}")
        try:
            ride.run_entries(client, state, excluded_tickers, STATE_LOCK, DRY_RUN, log_trade, ensure_shard_funds)
        except Exception as e:
            print(f"[RIDE] entry pass failed (non-fatal, other strategies continue): {e}")
        try:
            pulse_v2.run(client)   # PAPER ONLY -- simulated Pulse v2 next to the live bot, never sends orders
        except Exception as e:
            print(f"[V2-PAPER] pass failed (non-fatal): {e}")
        try:
            markets_paper.run(client, place_exit)   # commodity / currency 15-min + hourly markets (2 shares, live)
        except Exception as e:
            print(f"[MKTS-PAPER] pass failed (non-fatal): {e}")

        if time.time() - last_balance_sync >= BALANCE_SYNC_SECONDS:
            try:
                real_balance = client.get_balance()
                if "balance" in real_balance:
                    bankroll_and_breaker["bankroll"] = real_balance["balance"] / 100
            except Exception as e:
                print(f"Balance resync failed ({e}) -- keeping the current in-memory bankroll for now.")
            last_balance_sync = time.time()

        scan_results = []
        pending_candidates = []
        btc_87_tier_tickers = set()
        xrp_87_tier_tickers = set()
        sol_87_tier_tickers = set()
        doge_87_tier_tickers = set()
        bnb_87_tier_tickers = set()
        bch_87_tier_tickers = set()
        eth_87_tier_tickers = set()
        hype_87_tier_tickers = set()
        latch2_90_tier_tickers = set()
        triple_90_tier_tickers = set()
        early_60_tier_tickers = set()
        latch_90_tier_tickers = set()
        generic_tier_tickers = set()
        cfx_tier_tickers = set()
        any_tier_tickers = set()

        markets = discover_crypto_markets(client)
        reversion.cleanup_stale_tickers({m["ticker"] for m in markets})
        squeeze_momentum.cleanup_stale_tickers({m["ticker"] for m in markets})

        crypto_87_up_prices = {}
        for _lean_market in markets:
            _lean_result = evaluate_crypto_market(_lean_market)
            if _lean_result is None:
                continue
            if _lean_result.coin in ("BTC", "XRP", "SOL", "DOGE", "BNB", "BCH", "ETH", "HYPE"):
                crypto_87_up_prices[_lean_result.coin] = _lean_result.market_price

        ALL_SIX_COINS = ["BTC", "XRP", "SOL", "DOGE", "BNB", "BCH", "ETH", "HYPE"]
        MIN_COINS_AGREEING = 4

        def all_other_cryptos_agree(side_is_up: bool, others: list) -> bool:
            agree_count = 0
            for _coin in ALL_SIX_COINS:
                _price = crypto_87_up_prices.get(_coin)
                if _price is None:
                    continue
                _coin_is_up = _price > 0.5
                if _coin_is_up == side_is_up:
                    agree_count += 1
            return agree_count >= MIN_COINS_AGREEING

        evaluated = 0
        for market in markets:
            result = evaluate_crypto_market(market)
            if result is None:
                continue
            evaluated += 1
            reversion_signal = reversion.record_and_score(result.ticker, result.market_price)
            scan_results.append({
                "ticker": result.ticker, "title": result.title, "coin": result.coin,
                "direction": result.direction, "market_price": result.market_price,
                "model_prob": result.model_prob, "edge_pct": result.edge_pct,
                "seconds_remaining": result.seconds_remaining, "volume": result.volume,
                "reversion_z": reversion_signal.z_score,
            })
            side_for_depth = "bid" if result.market_price >= 0.5 else "ask"
            shares_left = get_shares_available(client, result.ticker, side_for_depth)
            shares_str = f"{shares_left}" if shares_left is not None else "?"
            print(
                f"  [{result.coin}] {result.title[:45]:<45} up=${result.market_price:.2f} "
                f"down=${1 - result.market_price:.2f} model={result.model_prob:.2f} "
                f"edge={result.edge_pct:+.1f}pp shares_left={shares_str} t-{int(result.seconds_remaining)}s"
            )
            if result.ticker in state or result.ticker in excluded_tickers:
                if result.ticker in excluded_tickers:
                    print(f"  {result.ticker} qualifies on price/time but was EXCLUDED earlier this "
                          f"session (likely a prior definitive order failure) -- not retrying.")
                continue
            live_bankroll_for_floor = get_fresh_balance_for_floor_check(client, bankroll)
            if live_bankroll_for_floor <= MIN_BANKROLL_FLOOR:
                print(f"Bankroll (${live_bankroll_for_floor:.2f}) at or below floor -- skipping new entries.")
                continue
            if result.close_time is not None and result.close_time.date() != datetime.now(timezone.utc).date():
                continue
            if not (9 <= result.seconds_remaining <= 4005):
                continue
            if result.is_hourly and result.seconds_remaining > 900:
                continue
            decision = decide_entry_side_and_price(result.seconds_remaining, result.market_price)
            if decision is None and result.coin == "BTC" and result.seconds_remaining < BTC_87_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= BTC_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or result.edge_pct > MIN_EDGE_FOR_87_TIERS):
                    decision = ("bid", up_price)
                    btc_87_tier_tickers.add(result.ticker)
                elif down_price >= BTC_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or (-result.edge_pct) > MIN_EDGE_FOR_87_TIERS):
                    decision = ("ask", down_price)
                    btc_87_tier_tickers.add(result.ticker)
            if decision is None and result.coin == "XRP" and result.seconds_remaining < XRP_87_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= XRP_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or result.edge_pct > MIN_EDGE_FOR_87_TIERS):
                    decision = ("bid", up_price)
                    xrp_87_tier_tickers.add(result.ticker)
                elif down_price >= XRP_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or (-result.edge_pct) > MIN_EDGE_FOR_87_TIERS):
                    decision = ("ask", down_price)
                    xrp_87_tier_tickers.add(result.ticker)
            if decision is None and result.coin == "SOL" and result.seconds_remaining < SOL_87_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= SOL_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or result.edge_pct > MIN_EDGE_FOR_87_TIERS):
                    decision = ("bid", up_price)
                    sol_87_tier_tickers.add(result.ticker)
                elif down_price >= SOL_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or (-result.edge_pct) > MIN_EDGE_FOR_87_TIERS):
                    decision = ("ask", down_price)
                    sol_87_tier_tickers.add(result.ticker)
            if decision is None and result.coin == "DOGE" and result.seconds_remaining < DOGE_87_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= DOGE_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or result.edge_pct > MIN_EDGE_FOR_87_TIERS):
                    decision = ("bid", up_price)
                    doge_87_tier_tickers.add(result.ticker)
                elif down_price >= DOGE_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or (-result.edge_pct) > MIN_EDGE_FOR_87_TIERS):
                    decision = ("ask", down_price)
                    doge_87_tier_tickers.add(result.ticker)
            if decision is None and result.coin == "BNB" and result.seconds_remaining < BNB_87_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= BNB_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or result.edge_pct > MIN_EDGE_FOR_87_TIERS):
                    decision = ("bid", up_price)
                    bnb_87_tier_tickers.add(result.ticker)
                elif down_price >= BNB_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or (-result.edge_pct) > MIN_EDGE_FOR_87_TIERS):
                    decision = ("ask", down_price)
                    bnb_87_tier_tickers.add(result.ticker)
            if decision is None and result.coin == "BCH" and result.seconds_remaining < BCH_87_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= BCH_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or result.edge_pct > MIN_EDGE_FOR_87_TIERS):
                    decision = ("bid", up_price)
                    bch_87_tier_tickers.add(result.ticker)
                elif down_price >= BCH_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or (-result.edge_pct) > MIN_EDGE_FOR_87_TIERS):
                    decision = ("ask", down_price)
                    bch_87_tier_tickers.add(result.ticker)
            if decision is None and result.coin == "ETH" and result.seconds_remaining < ETH_87_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= ETH_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or result.edge_pct > MIN_EDGE_FOR_87_TIERS):
                    decision = ("bid", up_price)
                    eth_87_tier_tickers.add(result.ticker)
                elif down_price >= ETH_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or (-result.edge_pct) > MIN_EDGE_FOR_87_TIERS):
                    decision = ("ask", down_price)
                    eth_87_tier_tickers.add(result.ticker)
            if decision is None and result.coin == "HYPE" and result.seconds_remaining < HYPE_87_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= HYPE_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or result.edge_pct > MIN_EDGE_FOR_87_TIERS):
                    decision = ("bid", up_price)
                    hype_87_tier_tickers.add(result.ticker)
                elif down_price >= HYPE_87_MIN_PRICE and (not EDGE_FILTER_ENABLED or (-result.edge_pct) > MIN_EDGE_FOR_87_TIERS):
                    decision = ("ask", down_price)
                    hype_87_tier_tickers.add(result.ticker)
            if decision is None and result.seconds_remaining < ANY_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if (up_price >= ANY_MIN_PRICE and (not CRYPTO_GATE_ENABLED or result.model_prob > ANY_MIN_MODEL_PROB)
                        and (not ANY_EDGE_FILTER_ENABLED or result.edge_pct > ANY_MIN_EDGE_PP)):
                    decision = ("bid", up_price)
                    any_tier_tickers.add(result.ticker)
                elif (down_price >= ANY_MIN_PRICE and (not CRYPTO_GATE_ENABLED or (1 - result.model_prob) > ANY_MIN_MODEL_PROB)
                        and (not ANY_EDGE_FILTER_ENABLED or (-result.edge_pct) > ANY_MIN_EDGE_PP)):
                    decision = ("ask", down_price)
                    any_tier_tickers.add(result.ticker)
            if (False and decision is None and result.coin in ("BTC", "XRP", "SOL", "DOGE", "BNB", "BCH", "ETH", "HYPE")
                    and result.seconds_remaining <= LATCH2_90_THRESHOLD_SECONDS):
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= LATCH2_90_MIN_PRICE:
                    decision = ("bid", up_price)
                    latch2_90_tier_tickers.add(result.ticker)
                elif down_price >= LATCH2_90_MIN_PRICE:
                    decision = ("ask", down_price)
                    latch2_90_tier_tickers.add(result.ticker)
            if False and decision is None and result.coin in ("XRP", "SOL", "DOGE") and result.seconds_remaining <= TRIPLE_90_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= TRIPLE_90_MIN_PRICE and all_other_cryptos_agree(side_is_up=True, others=["XRP", "SOL", "DOGE"]):
                    decision = ("bid", up_price)
                    triple_90_tier_tickers.add(result.ticker)
                elif down_price >= TRIPLE_90_MIN_PRICE and all_other_cryptos_agree(side_is_up=False, others=["XRP", "SOL", "DOGE"]):
                    decision = ("ask", down_price)
                    triple_90_tier_tickers.add(result.ticker)
            if decision is None:
                continue
            # CRYPTO GATE: every crypto entry (any tier) must have the model AND edge agree for the chosen side.
            _gate_side_model = result.model_prob if decision[0] == "bid" else (1 - result.model_prob)
            _gate_side_edge = result.edge_pct if decision[0] == "bid" else (-result.edge_pct)
            if CRYPTO_GATE_ENABLED and not (_gate_side_model > CRYPTO_GATE_MIN_MODEL and _gate_side_edge > CRYPTO_GATE_MIN_EDGE_PP):
                continue
            if decision[1] > ENTRY_MAX_PRICE + 1e-9:
                continue   # above the entry band
            candidate_side, candidate_price = decision
            liquidity = get_shares_available(client, result.ticker, candidate_side)
            if liquidity is not None and liquidity < MIN_SHARES_LIQUIDITY_REQUIRED:
                print(f"  Skipping {result.ticker} -- only {liquidity} shares available on the "
                      f"{candidate_side} side (need {MIN_SHARES_LIQUIDITY_REQUIRED}+).")
                continue
            pending_candidates.append(RankedCandidate(
                ticker=result.ticker, side=candidate_side, trade_price=candidate_price,
                potential_points=potential_points(candidate_price), original=result,
            ))

        _cfx_groups = []
        if CFX_COMMODITIES_ENABLED:
            _cfx_groups.append(("commodity", discover_commodity_markets(client)))
        if CFX_FX_ENABLED:
            _cfx_groups.append(("fx", discover_fx_markets(client)))
        for _kind, _markets in _cfx_groups:
            for market in _markets:
                result = evaluate_commodity_market(market, with_model=CFX_MODEL_REQUIRED)
                if result is None:
                    continue
                evaluated += 1
                reversion_signal = reversion.record_and_score(result.ticker, result.market_price)
                scan_results.append({
                    "ticker": result.ticker, "title": result.title, "coin": result.commodity,
                    "direction": result.direction, "market_price": result.market_price,
                    "model_prob": result.model_prob, "edge_pct": result.edge_pct,
                    "seconds_remaining": result.seconds_remaining, "volume": result.volume,
                    "reversion_z": reversion_signal.z_score,
                })
                _m = f"model={result.model_prob:.2f} edge={result.edge_pct:+.1f}pp " if result.model_prob is not None else "model=n/a "
                print(f"  [{result.commodity}] {result.title[:45]:<45} up=${result.market_price:.2f} "
                      f"down=${1 - result.market_price:.2f} {_m}t-{int(result.seconds_remaining)}s")
                if result.ticker in state or result.ticker in excluded_tickers:
                    continue
                if len(state) >= RISK_PARAMS.max_open_positions:
                    continue
                if not (9 <= result.seconds_remaining < CFX_87_THRESHOLD_SECONDS):
                    continue
                up_price = result.market_price
                down_price = 1 - result.market_price
                decision = None
                if CFX_MODEL_REQUIRED and (up_price >= CFX_87_MIN_PRICE or down_price >= CFX_87_MIN_PRICE) \
                        and (result.model_prob is None or result.edge_pct is None):
                    if result.ticker not in _cfx_nomodel_logged:
                        _cfx_nomodel_logged.add(result.ticker)
                        print(f"  Skipping {result.ticker} -- price qualifies but no model probability is available "
                              f"(price feed down/stale/wrong feed) and the model is required.")
                    continue
                if up_price >= CFX_87_MIN_PRICE and (not CFX_MODEL_REQUIRED or (
                        result.model_prob > CFX_MIN_MODEL_PROB and result.edge_pct > CFX_MIN_EDGE_PP)):
                    decision = ("bid", up_price)
                elif down_price >= CFX_87_MIN_PRICE and (not CFX_MODEL_REQUIRED or (
                        (1 - result.model_prob) > CFX_MIN_MODEL_PROB and (-result.edge_pct) > CFX_MIN_EDGE_PP)):
                    decision = ("ask", down_price)
                if decision is None:
                    continue
                if decision[1] > ENTRY_MAX_PRICE + 1e-9:
                    continue   # above the entry band
                candidate_side, candidate_price = decision
                liquidity = get_shares_available(client, result.ticker, candidate_side)
                if liquidity is not None and liquidity < MIN_SHARES_LIQUIDITY_REQUIRED:
                    print(f"  Skipping {result.ticker} -- only {liquidity} shares available on the "
                          f"{candidate_side} side (need {MIN_SHARES_LIQUIDITY_REQUIRED}+).")
                    continue
                cfx_tier_tickers.add(result.ticker)
                pending_candidates.append(RankedCandidate(
                    ticker=result.ticker, side=candidate_side, trade_price=candidate_price,
                    potential_points=potential_points(candidate_price), original=result,
                ))

        index_markets = discover_index_markets(client)
        for market in index_markets:
            result = evaluate_index_market(market)
            if result is None:
                continue
            evaluated += 1
            reversion_signal = reversion.record_and_score(result.ticker, result.market_price)
            scan_results.append({
                "ticker": result.ticker, "title": result.title, "coin": result.index,
                "direction": None, "market_price": result.market_price,
                "model_prob": None, "edge_pct": None,
                "seconds_remaining": result.seconds_remaining, "volume": result.volume,
                "reversion_z": reversion_signal.z_score,
            })

        GENERIC_CATEGORY_ALLOWLIST = set()
        if GENERIC_CATEGORY_ALLOWLIST:
            generic_series_unfiltered = get_cached_generic_short_markets(client)
            print(f"Generic market discovery (BEFORE category filter): {len(generic_series_unfiltered)} "
                  f"total series found. Real categories present: "
                  f"{sorted(set(generic_series_unfiltered.values())) if generic_series_unfiltered else 'NONE FOUND AT ALL'}")
            generic_series = {prefix: category for prefix, category in generic_series_unfiltered.items()
                               if category in GENERIC_CATEGORY_ALLOWLIST}
            print(f"Generic market discovery (AFTER filtering to {GENERIC_CATEGORY_ALLOWLIST}): "
                  f"{len(generic_series)} series to scan")
        else:
            generic_series = {}
        for prefix, category in generic_series.items():
            try:
                resp = client.get_markets(status="open", series_ticker=prefix, limit=50)
            except Exception as e:
                print(f"Generic market fetch failed for {prefix}: {e}")
                continue
            for market in resp.get("markets", []):
                open_time_str = market.get("open_time")
                close_time_str = market.get("close_time")
                if not open_time_str or not close_time_str:
                    continue
                try:
                    open_time = datetime.fromisoformat(open_time_str.replace("Z", "+00:00"))
                    close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                duration_seconds = (close_time - open_time).total_seconds()
                if not (55 * 60 <= duration_seconds <= 65 * 60):
                    continue
                result = evaluate_generic_market(market, category)
                if result is None:
                    continue
                evaluated += 1
                scan_results.append({
                    "ticker": result.ticker, "title": result.title, "coin": result.category,
                    "direction": None, "market_price": result.market_price,
                    "model_prob": None, "edge_pct": None,
                    "seconds_remaining": result.seconds_remaining, "volume": result.volume,
                    "reversion_z": None,
                })
                if result.ticker in state or result.ticker in excluded_tickers:
                    continue
                live_bankroll_for_floor = get_fresh_balance_for_floor_check(client, bankroll)
                if live_bankroll_for_floor <= MIN_BANKROLL_FLOOR:
                    continue
                if len(state) >= RISK_PARAMS.max_open_positions:
                    continue
                if result.close_time is not None and result.close_time.date() != datetime.now(timezone.utc).date():
                    continue
                if not (9 <= result.seconds_remaining <= GENERIC_MAX_SECONDS_REMAINING):
                    continue
                up_price = result.market_price
                down_price = 1 - result.market_price
                decision = None
                if GENERIC_ENTRY_MIN_PRICE <= up_price <= GENERIC_ENTRY_MAX_PRICE:
                    decision = ("bid", up_price)
                    generic_tier_tickers.add(result.ticker)
                elif GENERIC_ENTRY_MIN_PRICE <= down_price <= GENERIC_ENTRY_MAX_PRICE:
                    decision = ("ask", down_price)
                    generic_tier_tickers.add(result.ticker)
                if decision is None:
                    continue
                candidate_side, candidate_price = decision
                liquidity = get_shares_available(client, result.ticker, candidate_side)
                if liquidity is not None and liquidity < MIN_SHARES_LIQUIDITY_REQUIRED:
                    continue
                pending_candidates.append(RankedCandidate(
                    ticker=result.ticker, side=candidate_side, trade_price=candidate_price,
                    potential_points=potential_points(candidate_price), original=result,
                ))

        for candidate in rank_candidates(pending_candidates):
            result = candidate.original
            reversion_signal = reversion.record_and_score(result.ticker, result.market_price)
            side = candidate.side
            if side == "bid":
                real_fill_price = getattr(result, "yes_ask", candidate.trade_price)
            else:
                real_fill_price = getattr(result, "yes_bid", candidate.trade_price)
            real_fill_price = max(0.01, real_fill_price)
            # Real cost per share: the YES price for a bid, but (1 - YES bid)
            # for a NO buy (ask). The price ceiling and the capital cap
            # must use this number, not the raw YES price.
            entry_cost_per_share = real_fill_price if side == "bid" else (1 - real_fill_price)
            if entry_cost_per_share > ENTRY_HARD_CEILING:
                continue
            trade_price = real_fill_price
            if len(state) >= RISK_PARAMS.max_open_positions:
                continue
            entry_reason = "price_range"
            if candidate.ticker in btc_87_tier_tickers:
                count = BTC_87_SHARES
            elif candidate.ticker in xrp_87_tier_tickers:
                count = XRP_87_SHARES
            elif candidate.ticker in sol_87_tier_tickers:
                count = SOL_87_SHARES
            elif candidate.ticker in doge_87_tier_tickers:
                count = DOGE_87_SHARES
            elif candidate.ticker in bnb_87_tier_tickers:
                count = BNB_87_SHARES
            elif candidate.ticker in bch_87_tier_tickers:
                count = BCH_87_SHARES
            elif candidate.ticker in eth_87_tier_tickers:
                count = ETH_87_SHARES
            elif candidate.ticker in hype_87_tier_tickers:
                count = HYPE_87_SHARES
            elif candidate.ticker in any_tier_tickers:
                count = ANY_SHARES
                entry_reason = "any_300"
            elif candidate.ticker in cfx_tier_tickers:
                count = CFX_87_SHARES
                entry_reason = "commodity_fx_87"
            elif candidate.ticker in latch2_90_tier_tickers:
                count = LATCH2_90_SHARES
                entry_reason = "latch2_90"
            elif candidate.ticker in generic_tier_tickers:
                count = GENERIC_SHARES
                entry_reason = "generic_under_4h"
            else:
                count = TRIAL_SHARES_PER_TRADE

            TOTAL_CAPITAL_SAFETY_MARGIN_DOLLARS = 0.50
            live_balance_for_cap = get_fresh_balance_for_floor_check(client, bankroll)
            with STATE_LOCK:
                already_committed_dollars = sum(
                    p.get("entry_price", 0) * p.get("count", 0) for p in state.values()
                )
            this_trade_cost = entry_cost_per_share * count
            # Kalshi's live balance ALREADY excludes cash tied up in open positions,
            # so subtracting 'committed' again double-counted it -- and positions whose
            # markets had expired but not yet finalized kept blocking all new entries.
            available_for_new_trade = live_balance_for_cap - TOTAL_CAPITAL_SAFETY_MARGIN_DOLLARS
            if this_trade_cost > available_for_new_trade + 1e-9:
                print(f"  Skipping {result.ticker} -- total capital cap: live balance ${live_balance_for_cap:.2f}, "
                      f"already committed ${already_committed_dollars:.2f} across {len(state)} open position(s), "
                      f"this trade needs ${this_trade_cost:.2f}, only ${available_for_new_trade:.2f} available.")
                continue

            entry_dry_run = True if (candidate.ticker in early_60_tier_tickers
                                      or candidate.ticker in latch_90_tier_tickers) else DRY_RUN
            ORDER_RETRY_ATTEMPTS = 3
            ORDER_RETRY_DELAY_SECONDS = 0.5
            order_succeeded = False
            last_error = None
            if not entry_dry_run:
                # LIVE PRICE RE-CHECK: the scan loop takes ~15 seconds, so the price this decision was based on can be
                # stale by the time the order goes out. Because the order is a limit order priced at the OLD quote, it
                # fills at whatever the CURRENT price is -- even far below the floor if the market just moved (that is how
                # a "0.91" bid filled around 0.755 on EUR/USD). So re-read the live quote right before ordering, skip if
                # it no longer meets the floor, and price the limit at the live quote.
                try:
                    _lm = client.get_market(result.ticker).get("market", {})
                    _ya, _yb = _lm.get("yes_ask_dollars"), _lm.get("yes_bid_dollars")
                    if _ya is not None and _yb is not None:
                        _ya, _yb = float(_ya), float(_yb)
                        _live_mid = (_ya + _yb) / 2
                        _live_side_price = _live_mid if side == "bid" else 1 - _live_mid
                        _live_cost = _ya if side == "bid" else 1 - _yb
                        _live_spread = _ya - _yb
                        if _live_spread > ENTRY_MAX_SPREAD + 1e-9:
                            print(f"  SPREAD FILTER: {result.ticker} bid-ask gap is {_live_spread:.2f} (bid {_yb:.2f} / ask {_ya:.2f}), "
                                  f"wider than {ENTRY_MAX_SPREAD:.2f} -- skipping, a stop-loss here would sell far below the quote.")
                            continue
                        if _live_side_price < ENTRY_RECHECK_MIN_PRICE or _live_cost > ENTRY_HARD_CEILING:
                            print(f"  PRICE RE-CHECK: {result.ticker} {side.upper()} moved to {_live_side_price:.2f} "
                                  f"(cost {_live_cost:.2f}) since the scan -- below the {ENTRY_RECHECK_MIN_PRICE:.2f} floor or above "
                                  f"the entry-band ceiling now, skipping.")
                            continue
                        trade_price = _ya if side == "bid" else _yb
                except Exception as _e:
                    print(f"  PRICE RE-CHECK: couldn't read the live quote for {result.ticker} ({_e}) -- skipping rather than "
                          f"ordering on a stale price.")
                    continue
                # ENTRY SAFETY CHECK (pairs with the exit safety check): if Kalshi already shows a position in this
                # market -- e.g. a second overlapping copy of the bot just entered it -- do not enter again.
                try:
                    _pos_resp = client.get_positions()
                    _held = [p for p in _pos_resp.get("market_positions", _pos_resp.get("positions", []))
                             if p.get("ticker") == result.ticker
                             and float(p.get("position_fp", p.get("position", 0)) or 0) != 0]
                    if _held:
                        print(f"ENTRY SAFETY: {result.ticker} -- Kalshi already shows a position here, not entering again "
                              f"(it will be picked up and protected by the adopt step).")
                        continue
                except Exception as _e:
                    print(f"ENTRY SAFETY: couldn't verify existing positions for {result.ticker} ({_e}) -- continuing.")
                if flip_star.FLIP_HALTED:
                    print(f"  KILL SWITCH: {result.ticker} -- Pulse is halted after 3 losses, not entering (restart to resume).")
                    continue
                try:
                    ensure_shard_funds(client, result.ticker, this_trade_cost)
                except Exception:
                    continue   # couldn't confirm the shard has the cash -- skip this pass, retry next scan
            for attempt in range(1, ORDER_RETRY_ATTEMPTS + 1):
                try:
                    if side == "bid":
                        place_entry_yes(client, result.ticker, trade_price, count, entry_dry_run)
                    else:
                        place_entry_no(client, result.ticker, 1 - trade_price, count, entry_dry_run)
                    order_succeeded = True
                    break
                except Exception as e:
                    last_error = e
                    if "market_not_found" in str(e) or "market_closed" in str(e):
                        print(f"Entry order for {result.ticker} failed with a definitive error ({e}) -- "
                              f"not retrying, excluding this ticker for the rest of the session.")
                        excluded_tickers.add(result.ticker)
                        break
                    if "insufficient_balance" in str(e):
                        print(f"Entry order for {result.ticker} rejected: insufficient balance -- not retrying.")
                        try:
                            print(f"  DIAG raw /portfolio/balance: {client.get_balance()}")
                            print(f"  DIAG resting orders: {client._request('GET', '/portfolio/orders', params={'status': 'resting'})}")
                        except Exception as diag_e:
                            print(f"  DIAG lookup failed: {diag_e}")
                        _floor_check_cache[0] = 0.0
                        _floor_check_cache[1] = time.time()
                        break
                    if attempt < ORDER_RETRY_ATTEMPTS:
                        print(f"Entry order for {result.ticker} failed on attempt {attempt} ({e}) -- retrying...")
                        time.sleep(ORDER_RETRY_DELAY_SECONDS)
            if not order_succeeded:
                if result.ticker not in excluded_tickers:
                    print(f"Entry order for {result.ticker} failed after {ORDER_RETRY_ATTEMPTS} attempts ({last_error}) -- skipping.")
                continue
            if _floor_check_cache[0] is not None:
                _floor_check_cache[0] = max(0.0, _floor_check_cache[0] - this_trade_cost)
            with STATE_LOCK:
                tracked_entry_price = trade_price if side == "bid" else (1 - trade_price)
                state[result.ticker] = {"count": count, "entry_price": tracked_entry_price, "side": side,
                                         "dry_run": entry_dry_run, "peak_gain_per_contract": 0.0,
                                         "entry_time": time.time(), "coin": getattr(result, "coin", None) or getattr(result, "commodity", None),
                                         "latch_90_tier": candidate.ticker in latch_90_tier_tickers,
                                         "latch2_90_tier": candidate.ticker in latch2_90_tier_tickers,
                                         "triple_90_tier": candidate.ticker in triple_90_tier_tickers,
                                         "btc_87_tier": candidate.ticker in btc_87_tier_tickers,
                                         "xrp_87_tier": candidate.ticker in xrp_87_tier_tickers,
                                         "sol_87_tier": candidate.ticker in sol_87_tier_tickers,
                                         "doge_87_tier": candidate.ticker in doge_87_tier_tickers,
                                         "bnb_87_tier": candidate.ticker in bnb_87_tier_tickers,
                                         "bch_87_tier": candidate.ticker in bch_87_tier_tickers,
                                         "eth_87_tier": candidate.ticker in eth_87_tier_tickers,
                                         "hype_87_tier": candidate.ticker in hype_87_tier_tickers,
                                         "generic_tier": candidate.ticker in generic_tier_tickers,
                                         "commodity_fx_tier": candidate.ticker in cfx_tier_tickers,
                                         "any_tier": candidate.ticker in any_tier_tickers}
            if not entry_dry_run:
                # FILL-PRICE GUARD: a limit order only caps what we pay; if the book moves between the live re-check and
                # the fill, Kalshi fills at the new (different) price. Read the REAL fill and, if it is outside the
                # tolerance or under the floor, exit immediately instead of holding a trade the rules would never take.
                real_cost = actual_fill_cost(client, result.ticker)
                if real_cost is not None:
                    with STATE_LOCK:
                        if result.ticker in state:
                            state[result.ticker]["entry_price"] = real_cost
                            if real_cost < ENTRY_RECHECK_MIN_PRICE - 1e-9 or abs(real_cost - tracked_entry_price) > FILL_PRICE_TOLERANCE + 1e-9:
                                state[result.ticker]["force_exit"] = True
                    if real_cost < ENTRY_RECHECK_MIN_PRICE - 1e-9 or abs(real_cost - tracked_entry_price) > FILL_PRICE_TOLERANCE + 1e-9:
                        print(f"  FILL OUTSIDE TOLERANCE: {result.ticker} intended ${tracked_entry_price:.2f} but Kalshi filled at "
                              f"${real_cost:.2f} (floor ${ENTRY_RECHECK_MIN_PRICE:.2f}, tolerance ${FILL_PRICE_TOLERANCE:.2f}) -- exiting immediately.")
                    else:
                        print(f"  FILL CHECK OK: {result.ticker} intended ${tracked_entry_price:.2f}, real fill ${real_cost:.2f}.")
                else:
                    print(f"  FILL CHECK: couldn't read the real fill price for {result.ticker} -- keeping the intended entry price.")
            log_trade(result, side, trade_price, count, entry_dry_run, reversion_z=reversion_signal.z_score, entry_reason=entry_reason)

        with STATE_LOCK:
            save_json(STATE_FILE, state)
        save_json(SCAN_FILE, {
            "generated_at": time.time(), "results": scan_results, "bankroll": bankroll,
            "markets_scanned": len(markets), "markets_evaluated": evaluated,
            "dry_run": DRY_RUN,
        })
        try:
            build_dashboard_data.build()
        except Exception as e:
            print(f"Dashboard data rebuild failed (non-fatal, trading continues): {e}")
        push_dashboard_data_to_server()
        print(f"Scanned {len(markets)} market(s); {evaluated} evaluated; {len(state)} open position(s) tracked "
              f"({_filled_count[0]} confirmed filled, {_unfilled_count[0]} still resting/unfilled, "
              f"{_unclear_count[0]} unclear -- all-time this session).")
        try:
            if trend_report is not None:
                trend_report.maybe_run()
        except Exception as e:
            print(f"[TREND] hook failed (non-fatal): {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
