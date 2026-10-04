"""
Kalshi crypto scalper — trades KXBTC15M-style up/down contracts using a
volatility-priced probability model instead of guessing from price alone.
READ THIS FIRST:
  - DRY_RUN = True by default. No orders placed until you flip it.
  - KALSHI_ENV=demo in .env by default — use it. These markets settle in
    minutes, so mistakes compound fast; there's no reason to skip testing.
  - This is a genuinely harder niche than the sports version: 15-minute
    crypto binaries are actively traded by high-frequency firms with far
    lower latency than a Python script polling over HTTP. The volatility
    model gives a real, defensible probability estimate — it does not
    give you a speed advantage over faster competitors. Expect this to
    be a much closer contest than the sports edge-detection approach.
  - Fees and bid-ask spread eat into small edges fast at this trade
    frequency. min_edge_pct is set higher here than in the sports bot on
    purpose — don't lower it without a real reason to trust smaller
    signals survive costs.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
from pathlib import Path
import requests
from kalshi_client import KalshiClient
from strategy import discover_crypto_markets, evaluate_crypto_market, \
    discover_generic_short_markets, evaluate_generic_market, \
    discover_commodity_markets, evaluate_commodity_market, \
    discover_index_markets, evaluate_index_market
from priority import RankedCandidate, potential_points, rank_candidates, size_scale_factor_91_95
from risk import RiskParams, CircuitBreaker, kelly_size
from exits import check_exit, current_position_value
import build_dashboard_data
import reversion
import price_feed
import squeeze_momentum
import shadow_exit_tracking as sht
DRY_RUN = False
EXIT_DRY_RUN = False
POLL_SECONDS = 1
STARTING_BANKROLL = 10.0
MIN_BANKROLL_FLOOR = 0.50
DAILY_PROFIT_TARGET = 2000.0
RISK_PARAMS = RiskParams(
    kelly_fraction=0.15,
    min_edge_pct=0.0,
    max_position_pct=3.0,
    max_open_positions=35,
    max_daily_loss_pct=20.0,
)
REVERSION_ENTRY_Z_THRESHOLD = -1.5
REVERSION_EXIT_Z_THRESHOLD = 1.0
PARTIAL_PROFIT_FRACTION = 0.55
ABSOLUTE_MIN_PRICE_FLOOR = 0.50
TRIAL_TIER1_THRESHOLD_SECONDS = 150
TRIAL_TIER1_MIN_PRICE = 0.98
TRIAL_TIER1_MAX_PRICE = 0.99
TRIAL_TIER2_THRESHOLD_SECONDS = 90
TRIAL_TIER2_MIN_PRICE = 0.98
TRIAL_TIER2_MAX_PRICE = 0.99
BTC_92_THRESHOLD_SECONDS = 240
BTC_92_MIN_PRICE = 0.93
EARLY_60_THRESHOLD_SECONDS = 800
EARLY_60_MIN_PRICE = 0.58
EARLY_60_MAX_PRICE = 0.62
EARLY_60_SHARES = 1
TRIAL_SHARES_PER_TRADE = 55
ASK_MAX_PRICE = 0.99
COIN_SIZE_MULTIPLIER = {}
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
    data = load_json(SCAN_FILE, None)
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
def place_entry(client: KalshiClient, ticker: str, side: str, price: float, count: float, dry_run: bool):
    print(f"{'DRY-RUN' if dry_run else 'PLACING'} {side.upper()} {ticker} x{count} @ ${price:.2f}")
    if dry_run:
        return {"dry_run": True}
    resp = client.create_order(
        ticker=ticker, client_order_id=str(uuid.uuid4()),
        side=side, count=str(count), price=f"{price:.2f}",
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
            print(f"  CONFIRMED FILLED: {ticker} {side.upper()} x{count} @ ${price:.2f} -- real position confirmed.")
        else:
            _unfilled_count[0] += 1
            print(f"  NOT YET FILLED: {ticker} {side.upper()} x{count} @ ${price:.2f} is resting on the order "
                  f"book, unfilled -- this is a real, open order, not a confirmed trade yet.")
    elif status in ("filled", "executed"):
        _filled_count[0] += 1
        print(f"  CONFIRMED FILLED: {ticker} {side.upper()} x{count} @ ${price:.2f} -- Kalshi reports this order as {status}.")
    else:
        _unclear_count[0] += 1
        print(f"  Fill status unclear (order status field: '{status}' -- Kalshi's exact response shape for this "
              f"wasn't independently verified). Treat this order as UNCONFIRMED until checked manually.")
    return resp
def place_exit(client: KalshiClient, ticker: str, close_side: str, count: float, dry_run: bool):
    price = 0.01 if close_side == "ask" else 0.99
    print(f"{'EXIT DRY-RUN' if dry_run else 'EXITING'} {close_side.upper()} {ticker} x{count} @ ~${price:.2f}")
    if dry_run:
        return {"dry_run": True}
    return client.create_order(
        ticker=ticker, client_order_id=str(uuid.uuid4()),
        side=close_side, count=str(count), price=f"{price:.2f}",
    )
_permanently_unexitable_tickers = set()


def evaluate_any_market_for_exit(market: dict):
    """UNIVERSAL fallback, per explicit request -- protects EVERY open
    Kalshi position with the stop-loss exit rule, not just crypto/
    commodity/index. Confirmed necessary via a real gap: a manually-
    placed sports bet matched neither evaluate_crypto_market() nor
    evaluate_commodity_market(), so check_exits() was silently skipping
    it every single cycle -- no stop-loss, nothing. This ran unprotected
    the whole time; the fix is to always be able to extract SOMETHING to
    check against, for any ticker Kalshi has, not just the specific
    categories this bot actively trades entries on.

    Deliberately has NO model_prob -- there's no volatility model for
    sports (or elections, weather, etc.), so check_exit()'s existing
    getattr(result, "model_prob", 0.5) fallback kicks in automatically.
    Rule 1 (stop-loss) -- the part that actually matters most for
    capping a real loss on ANY bet -- still applies in full, using the
    same peak-tracking, same pre-breakeven/breakeven-tight two-stage
    logic as everything else."""
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


def check_exits(client: KalshiClient, state: dict, excluded_tickers: set) -> list:
    """Re-evaluates every open position for the exit rules in exits.py.
    Returns realized PnL for any that exited early, removes them from
    state so check_settlements() doesn't later double-count them, and
    adds them to excluded_tickers so the entry logic can never
    immediately re-open the same position."""
    settlements = load_json(SETTLEMENTS_FILE, [])
    realized_pnl = []
    stop_loss_occurred_this_cycle = False
    for ticker in list(state.keys()):
        if ticker in _permanently_unexitable_tickers:
            continue
        position = state[ticker]
        try:
            market = client.get_market(ticker).get("market", {})
        except Exception:
            continue
        if market.get("status") == "finalized":
            continue
        result = evaluate_crypto_market(market)
        if result is None:
            # FIXED after a real bug: a Gold/Silver position's ticker
            # never matches evaluate_crypto_market, which silently
            # skipped the exit check entirely -- meaning a commodity
            # position would never get stop-loss protection at all.
            result = evaluate_commodity_market(market)
        if result is None:
            # FIXED after a second real bug, same class as the one
            # above -- a manually-placed sports (or any other
            # non-crypto/commodity) bet matched NEITHER evaluator, so
            # it ran with zero stop-loss protection the entire time.
            # This universal fallback means ANY Kalshi ticker -- sports,
            # elections, weather, anything -- gets at least the
            # stop-loss rule applied, even with no model.
            result = evaluate_any_market_for_exit(market)
        if result is None:
            continue
        reversion_signal = reversion.record_and_score(ticker, result.market_price)
        current_value = current_position_value(position, result.market_price)
        current_gain = current_value - position["entry_price"]
        position["peak_gain_per_contract"] = max(position.get("peak_gain_per_contract", 0.0), current_gain)
        decision = check_exit(position, result.market_price, getattr(result, "model_prob", 0.5), result.seconds_remaining,
                               reversion_z=reversion_signal.z_score, partial_profit_fraction=PARTIAL_PROFIT_FRACTION,
                               peak_gain_per_contract=position["peak_gain_per_contract"])
        if not decision.should_exit:
            continue
        position_is_real = position.get("dry_run") is False
        send_real_order = position_is_real and not EXIT_DRY_RUN
        close_side = "ask" if position["side"] == "bid" else "bid"
        try:
            place_exit(client, ticker, close_side, position["count"], dry_run=not send_real_order)
        except Exception as e:
            if "market_closed" in str(e) or "market_not_found" in str(e):
                _permanently_unexitable_tickers.add(ticker)
                print(f"{ticker} exit order can't ever succeed ({e}). Left tracked; waiting for "
                      f"check_settlements() to pick up the real result once Kalshi finishes settling it. "
                      f"No further exit attempts will be made on this ticker.")
            else:
                print(f"Exit order for {ticker} failed ({e}) -- leaving it tracked, will retry next loop.")
            continue
        if position_is_real and EXIT_DRY_RUN:
            print(f"  (real position -- EXIT_DRY_RUN means no real close order was sent; "
                  f"leaving it tracked to settle naturally instead of losing it)")
            continue
        exit_value = current_position_value_for_log(position, result.market_price)
        pnl = (exit_value - position["entry_price"]) * position["count"]
        settlements.append({
            "ticker": ticker, "title": result.title, "result": "early_exit",
            "side": position["side"], "count": position["count"], "entry_price": position["entry_price"],
            "exit_value": round(exit_value, 4), "exit_reason": decision.reason,
            "pnl": round(pnl, 2), "timestamp": time.time(), "dry_run": position.get("dry_run", True),
        })
        realized_pnl.append(pnl)
        print(f"Early exit {ticker}: {decision.reason}, pnl=${pnl:.2f}")
        sht.record_early_exit(ticker, position["side"], position["entry_price"], position["count"],
                               exit_value, decision.reason, category="crypto")
        del state[ticker]
        if not decision.reason.startswith("capture_profit"):
            excluded_tickers.add(ticker)
        if decision.reason.startswith("stop_loss") and not position.get("dry_run", True):
            stop_loss_occurred_this_cycle = True
    if stop_loss_occurred_this_cycle:
        _stop_loss_count[0] += 1
        print(f"Stop-loss count this session: {_stop_loss_count[0]}")
    if realized_pnl:
        save_json(SETTLEMENTS_FILE, settlements)
    if _stop_loss_count[0] > 4:
        save_json(STATE_FILE, state)
        print("\n" + "=" * 60)
        print(f"STOP LOSS COUNT ({_stop_loss_count[0]}) EXCEEDED 4 -- SHUTTING DOWN PER CIRCUIT BREAKER.")
        print("The bot will not place or manage any further trades")
        print("until you manually restart it.")
        print("=" * 60)
        try:
            from email_alert import send_email_alert, send_sms_alert
            send_email_alert(
                "Pulse circuit breaker tripped -- bot stopped",
                f"Pulse's stop-loss count reached {_stop_loss_count[0]} this session and the "
                f"circuit breaker shut the bot down. It will not trade again until you "
                f"manually restart it with 'python3 bot.py'."
            )
            send_sms_alert(
                "Pulse circuit breaker tripped",
                f"Stop-loss count reached {_stop_loss_count[0]}. Bot has stopped trading."
            )
        except Exception as e:
            print(f"Email/SMS alert attempt failed ({e}) -- continuing with shutdown regardless.")
        sys.exit(1)
    return realized_pnl
def current_position_value_for_log(position: dict, market_price: float) -> float:
    return market_price if position["side"] == "bid" else (1 - market_price)
def check_settlements(client: KalshiClient, state: dict) -> list:
    settlements = load_json(SETTLEMENTS_FILE, [])
    newly_settled_pnl = []
    for ticker in list(state.keys()):
        try:
            market = client.get_market(ticker).get("market", {})
        except Exception:
            continue
        if market.get("status") != "finalized" or not market.get("result"):
            continue
        position = state.pop(ticker)
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
    if newly_settled_pnl:
        save_json(SETTLEMENTS_FILE, settlements)
    return newly_settled_pnl
_floor_check_cache = [None, 0.0]
_stop_loss_count = [0]
FLOOR_CHECK_CACHE_SECONDS = 30
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
def decide_entry_side_and_price(seconds_remaining: float, market_price: float):
    secs = seconds_remaining
    up_price = market_price
    down_price = 1 - market_price
    if secs <= TRIAL_TIER2_THRESHOLD_SECONDS:
        floor, cap = TRIAL_TIER2_MIN_PRICE, TRIAL_TIER2_MAX_PRICE
    elif secs <= TRIAL_TIER1_THRESHOLD_SECONDS:
        floor, cap = TRIAL_TIER1_MIN_PRICE, TRIAL_TIER1_MAX_PRICE
    else:
        return None
    if floor <= up_price <= cap:
        return "bid", up_price
    elif floor <= down_price <= cap:
        return "ask", down_price
    return None
def adopt_manual_positions(client: KalshiClient, state: dict) -> None:
    try:
        positions_resp = client.get_positions()
    except Exception as e:
        print(f"Couldn't check for manual positions to adopt ({e}) -- skipping this cycle's check.")
        return
    real_positions = positions_resp.get("market_positions", positions_resp.get("positions", []))
    for p in real_positions:
        ticker = p.get("ticker")
        if not ticker or ticker in state:
            continue
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
                  f"looks unreasonable, not adopting rather than risk a broken stop-loss.")
            continue
        side = "bid" if position_fp > 0 else "ask"
        state[ticker] = {"count": count, "entry_price": entry_price, "side": side,
                          "dry_run": False, "peak_gain_per_contract": 0.0,
                          "entry_time": time.time(), "manually_adopted": True}
        print(f"  ADOPTED manual position: {ticker} {side.upper()} x{count:.0f} @ ~${entry_price:.2f} "
              f"(derived from Kalshi's real position data) -- trailing stop-loss now protecting it.")
def run():
    client = KalshiClient()
    state = load_json(STATE_FILE, {})
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
    print(f"Bot started. DRY_RUN={DRY_RUN}. EXIT_DRY_RUN={EXIT_DRY_RUN}. Bankroll: ${bankroll:.2f}. "
          f"Today's PnL so far (recovered from settlements): ${breaker.day_pnl:.2f}")
    excluded_tickers = set()
    last_balance_sync = time.time()
    BALANCE_SYNC_SECONDS = 300
    last_deposit_check_time = time.time()
    while True:
        adopt_manual_positions(client, state)
        if time.time() - last_balance_sync >= BALANCE_SYNC_SECONDS:
            try:
                real_balance = client.get_balance()
                if "balance" in real_balance:
                    real_bankroll = real_balance["balance"] / 100
                    drift = real_bankroll - bankroll
                    deposit_total = 0.0
                    try:
                        deposits_resp = client.get_deposits(limit=20)
                        for d in deposits_resp.get("deposits", []):
                            amount_cents = d.get("amount_cents", d.get("amount"))
                            created = d.get("created_time", d.get("timestamp", ""))
                            if amount_cents is None:
                                continue
                            try:
                                created_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp() \
                                    if isinstance(created, str) and created else 0
                            except ValueError:
                                created_ts = 0
                            if created_ts >= last_deposit_check_time:
                                deposit_total += amount_cents / 100
                    except Exception:
                        pass
                    if abs(drift) >= 0.01:
                        remaining_drift = drift - deposit_total
                        if deposit_total > 0:
                            print(f"Bankroll resync: was tracking ${bankroll:.2f} in-memory, real balance is "
                                  f"${real_bankroll:.2f} (drift of ${drift:+.2f} -- ${deposit_total:.2f} of that "
                                  f"was a real deposit, ${remaining_drift:+.2f} is unexplained/fees) -- "
                                  f"correcting to match reality.")
                        else:
                            print(f"Bankroll resync: was tracking ${bankroll:.2f} in-memory, real balance is "
                                  f"${real_bankroll:.2f} (drift of ${drift:+.2f}, mostly real fees never "
                                  f"subtracted from the in-memory number) -- correcting to match reality.")
                    bankroll = real_bankroll
                    last_deposit_check_time = time.time()
            except Exception as e:
                print(f"Balance resync failed ({e}) -- keeping the current in-memory bankroll for now.")
            last_balance_sync = time.time()
            try:
                real_positions_resp = client.get_positions()
                position_list = real_positions_resp.get("market_positions", real_positions_resp.get("positions", []))
                real_open_tickers = set()
                for p in position_list:
                    ticker = p.get("ticker")
                    qty = p.get("position", p.get("quantity", p.get("count")))
                    if ticker and qty is not None and qty != 0:
                        real_open_tickers.add(ticker)
                if position_list:
                    stale_tickers = [t for t in state if t not in real_open_tickers]
                    for t in stale_tickers:
                        print(f"Position sync: {t} is tracked locally but Kalshi shows it's no longer "
                              f"open -- removing from local tracking (it already resolved on Kalshi's "
                              f"side; check_settlements should have caught this, but this catches "
                              f"anything that slipped through).")
                        del state[t]
                    unknown_tickers = [t for t in real_open_tickers if t not in state]
                    for t in unknown_tickers:
                        print(f"Position sync WARNING: Kalshi shows {t} as open, but it's not in local "
                              f"tracking at all -- this position exists but this bot doesn't know its "
                              f"entry price or side, so it can't be managed. Check it manually.")
                    if stale_tickers:
                        save_json(STATE_FILE, state)
                else:
                    print(f"Position sync: couldn't recognize the response shape -- raw keys were "
                          f"{list(real_positions_resp.keys())}. Skipping this cycle's sync.")
            except Exception as e:
                print(f"Position sync failed ({e}) -- keeping local tracking as-is for now.")
        settled_pnls = check_settlements(client, state)
        for pnl in settled_pnls:
            bankroll += pnl
            breaker.record_pnl(pnl)
        if settled_pnls:
            save_json(STATE_FILE, state)
        exit_pnls = check_exits(client, state, excluded_tickers)
        for pnl in exit_pnls:
            bankroll += pnl
            breaker.record_pnl(pnl)
        if exit_pnls:
            save_json(STATE_FILE, state)
        shadow_comparisons = sht.check_shadow_positions(client)
        for c in shadow_comparisons:
            verdict = "GOOD EXIT" if c["difference"] > 0 else "BAD EXIT" if c["difference"] < 0 else "NEUTRAL"
            print(f"Exit outcome check [{c['exit_reason']}] {c['ticker']}: actual=${c['pnl_actual']:.2f} "
                  f"vs if_held=${c['pnl_if_held']:.2f} ({verdict}, diff=${c['difference']:+.2f})")
        scan_results = []
        pending_candidates = []
        btc_92_tier_tickers = set()
        early_60_tier_tickers = set()
        markets = discover_crypto_markets(client)
        reversion.cleanup_stale_tickers({m["ticker"] for m in markets})
        squeeze_momentum.cleanup_stale_tickers({m["ticker"] for m in markets})
        evaluated = 0
        for market in markets:
            result = evaluate_crypto_market(market)
            if result is None:
                continue
            evaluated += 1
            reversion_signal = reversion.record_and_score(result.ticker, result.market_price)
            momentum_signal = reversion.check_momentum_turnaround(
                result.ticker, 1 - result.market_price, result.seconds_remaining
            )
            if momentum_signal is not None:
                print(f"  MOMENTUM TRIAL (no bet placed): {result.ticker} 'no' side rose through "
                      f"~20%->~40%->{momentum_signal.no_price:.2f} with {int(momentum_signal.seconds_remaining)}s left.")
            recent_prices = price_feed.get_recent_prices(result.coin)
            if recent_prices:
                squeeze_signal = squeeze_momentum.check_squeeze_momentum(result.ticker, recent_prices)
                if squeeze_signal is not None:
                    print(f"  SQUEEZE MOMENTUM TRIAL (no bet placed): {result.ticker} squeeze released "
                          f"{squeeze_signal.direction} (momentum={squeeze_signal.momentum:+.2f}).")
            scan_results.append({
                "ticker": result.ticker, "title": result.title, "coin": result.coin,
                "direction": result.direction, "market_price": result.market_price,
                "model_prob": result.model_prob, "edge_pct": result.edge_pct,
                "seconds_remaining": result.seconds_remaining, "volume": result.volume,
                "reversion_z": reversion_signal.z_score,
            })
            print(
                f"  [{result.coin}] {result.title[:45]:<45} up=${result.market_price:.2f} "
                f"down=${1 - result.market_price:.2f} model={result.model_prob:.2f} "
                f"edge={result.edge_pct:+.1f}pp  t-{int(result.seconds_remaining)}s"
            )
            if result.ticker in state or result.ticker in excluded_tickers:
                continue
            live_bankroll_for_floor = get_fresh_balance_for_floor_check(client, bankroll)
            if live_bankroll_for_floor <= MIN_BANKROLL_FLOOR:
                print(f"Bankroll (${live_bankroll_for_floor:.2f}, live-checked) at or below the floor "
                      f"(${MIN_BANKROLL_FLOOR:.2f}) — skipping new entries.")
                continue
            if result.close_time is not None and result.close_time.date() != datetime.now(timezone.utc).date():
                continue
            if not (9 <= result.seconds_remaining <= 4005):
                continue
            if result.is_hourly and result.seconds_remaining > 900:
                continue
            decision = decide_entry_side_and_price(result.seconds_remaining, result.market_price)
            if False and decision is None and result.coin == "BTC" and result.seconds_remaining < BTC_92_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if up_price >= BTC_92_MIN_PRICE:
                    decision = ("bid", up_price)
                    btc_92_tier_tickers.add(result.ticker)
                elif down_price >= BTC_92_MIN_PRICE:
                    decision = ("ask", down_price)
                    btc_92_tier_tickers.add(result.ticker)
            if decision is None and result.seconds_remaining > EARLY_60_THRESHOLD_SECONDS:
                up_price = result.market_price
                down_price = 1 - result.market_price
                if EARLY_60_MIN_PRICE <= up_price <= EARLY_60_MAX_PRICE:
                    decision = ("bid", up_price)
                    early_60_tier_tickers.add(result.ticker)
                elif EARLY_60_MIN_PRICE <= down_price <= EARLY_60_MAX_PRICE:
                    decision = ("ask", down_price)
                    early_60_tier_tickers.add(result.ticker)
            if decision is None:
                continue
            candidate_side, candidate_price = decision
            pending_candidates.append(RankedCandidate(
                ticker=result.ticker, side=candidate_side, trade_price=candidate_price,
                potential_points=potential_points(candidate_price), original=result,
            ))
        commodity_markets = discover_commodity_markets(client)
        for market in commodity_markets:
            result = evaluate_commodity_market(market)
            if result is None:
                continue
            evaluated += 1
            reversion_signal = reversion.record_and_score(result.ticker, result.market_price)
            scan_results.append({
                "ticker": result.ticker, "title": result.title, "coin": result.commodity,
                "direction": None, "market_price": result.market_price,
                "model_prob": None, "edge_pct": None,
                "seconds_remaining": result.seconds_remaining, "volume": result.volume,
                "reversion_z": reversion_signal.z_score,
            })
            print(
                f"  [{result.commodity}] {result.title[:45]:<45} up=${result.market_price:.2f} "
                f"down=${1 - result.market_price:.2f}  t-{int(result.seconds_remaining)}s"
            )
            if result.ticker in state or result.ticker in excluded_tickers:
                continue
            live_bankroll_for_floor = get_fresh_balance_for_floor_check(client, bankroll)
            if live_bankroll_for_floor <= MIN_BANKROLL_FLOOR:
                print(f"Bankroll (${live_bankroll_for_floor:.2f}, live-checked) at or below the floor "
                      f"(${MIN_BANKROLL_FLOOR:.2f}) — skipping new entries.")
                continue
            if result.close_time is not None and result.close_time.date() != datetime.now(timezone.utc).date():
                continue
            if not (9 <= result.seconds_remaining <= 4005):
                continue
            decision = decide_entry_side_and_price(result.seconds_remaining, result.market_price)
            if decision is None:
                continue
            candidate_side, candidate_price = decision
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
            print(
                f"  [{result.index}] {result.title[:45]:<45} up=${result.market_price:.2f} "
                f"down=${1 - result.market_price:.2f}  t-{int(result.seconds_remaining)}s"
            )
            if result.ticker in state or result.ticker in excluded_tickers:
                continue
            live_bankroll_for_floor = get_fresh_balance_for_floor_check(client, bankroll)
            if live_bankroll_for_floor <= MIN_BANKROLL_FLOOR:
                print(f"Bankroll (${live_bankroll_for_floor:.2f}, live-checked) at or below the floor "
                      f"(${MIN_BANKROLL_FLOOR:.2f}) — skipping new entries.")
                continue
            if result.close_time is not None and result.close_time.date() != datetime.now(timezone.utc).date():
                continue
            if not (9 <= result.seconds_remaining <= 4005):
                continue
            decision = decide_entry_side_and_price(result.seconds_remaining, result.market_price)
            if decision is None:
                continue
            candidate_side, candidate_price = decision
            pending_candidates.append(RankedCandidate(
                ticker=result.ticker, side=candidate_side, trade_price=candidate_price,
                potential_points=potential_points(candidate_price), original=result,
            ))
        generic_series = get_cached_generic_short_markets(client)
        for prefix, (category, frequency) in generic_series.items():
            try:
                resp = client.get_markets(status="open", series_ticker=prefix, limit=50)
            except Exception as e:
                print(f"Generic market fetch failed for {prefix}: {e}")
                continue
            for market in resp.get("markets", []):
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
                print(
                    f"  [{result.category}] {result.title[:45]:<45} price=${result.market_price:.2f} "
                    f"t-{int(result.seconds_remaining)}s (generic {frequency})"
                )
                if result.ticker in state or result.ticker in excluded_tickers:
                    continue
                live_bankroll_for_floor = get_fresh_balance_for_floor_check(client, bankroll)
                if live_bankroll_for_floor <= MIN_BANKROLL_FLOOR:
                    print(f"Bankroll (${live_bankroll_for_floor:.2f}, live-checked) at or below the floor "
                          f"(${MIN_BANKROLL_FLOOR:.2f}) — skipping new entries.")
                    continue
                if len(state) >= RISK_PARAMS.max_open_positions:
                    continue
                if result.close_time is not None and result.close_time.date() != datetime.now(timezone.utc).date():
                    continue
                if not (9 <= result.seconds_remaining <= 4005):
                    continue
                if frequency == "hourly" and result.seconds_remaining > 900:
                    continue
                decision = decide_entry_side_and_price(result.seconds_remaining, result.market_price)
                if decision is None:
                    continue
                candidate_side, candidate_price = decision
                pending_candidates.append(RankedCandidate(
                    ticker=result.ticker, side=candidate_side, trade_price=candidate_price,
                    potential_points=potential_points(candidate_price), original=result,
                ))
        for candidate in rank_candidates(pending_candidates):
            result = candidate.original
            reversion_signal = reversion.record_and_score(result.ticker, result.market_price)
            side = candidate.side
            trade_price = max(0.01, min(0.99, candidate.trade_price))
            if len(state) >= RISK_PARAMS.max_open_positions:
                continue
            entry_reason = "price_range"
            if candidate.ticker in btc_92_tier_tickers:
                count = 10
            elif candidate.ticker in early_60_tier_tickers:
                count = EARLY_60_SHARES
            else:
                count = TRIAL_SHARES_PER_TRADE
            entry_dry_run = True if candidate.ticker in early_60_tier_tickers else DRY_RUN
            ORDER_RETRY_ATTEMPTS = 3
            ORDER_RETRY_DELAY_SECONDS = 0.5
            order_succeeded = False
            last_error = None
            for attempt in range(1, ORDER_RETRY_ATTEMPTS + 1):
                try:
                    place_entry(client, result.ticker, side, trade_price, count, entry_dry_run)
                    order_succeeded = True
                    break
                except Exception as e:
                    last_error = e
                    if attempt < ORDER_RETRY_ATTEMPTS:
                        print(f"Entry order for {result.ticker} failed on attempt {attempt} ({e}) "
                              f"-- retrying in {ORDER_RETRY_DELAY_SECONDS}s...")
                        time.sleep(ORDER_RETRY_DELAY_SECONDS)
            if not order_succeeded:
                print(f"Entry order for {result.ticker} failed after {ORDER_RETRY_ATTEMPTS} attempts "
                      f"({last_error}) -- skipping this one, continuing to monitor existing positions.")
                continue
            state[result.ticker] = {"count": count, "entry_price": trade_price, "side": side,
                                     "dry_run": entry_dry_run, "peak_gain_per_contract": 0.0,
                                     "entry_time": time.time()}
            log_trade(result, side, trade_price, count, entry_dry_run, reversion_z=reversion_signal.z_score, entry_reason=entry_reason)
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
        time.sleep(POLL_SECONDS)
if __name__ == "__main__":
    run()
