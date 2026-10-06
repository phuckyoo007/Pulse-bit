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
from strategy import discover_crypto_markets, evaluate_crypto_market, \
    discover_generic_short_markets, evaluate_generic_market, \
    discover_commodity_markets, evaluate_commodity_market, discover_fx_markets, \
    discover_index_markets, evaluate_index_market, GENERIC_MAX_SECONDS_REMAINING
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
MIN_BANKROLL_FLOOR = 0.0
DAILY_PROFIT_TARGET = 2000.0
RISK_PARAMS = RiskParams(
    kelly_fraction=0.15,
    min_edge_pct=0.0,
    max_position_pct=3.0,
    max_open_positions=35,
    max_daily_loss_pct=20.0,
)
BTC_87_THRESHOLD_SECONDS = 180
BTC_87_MIN_PRICE = 0.88
BTC_87_SHARES = 2
XRP_87_THRESHOLD_SECONDS = 180
XRP_87_MIN_PRICE = 0.88
XRP_87_SHARES = 2
SOL_87_THRESHOLD_SECONDS = 180
SOL_87_MIN_PRICE = 0.88
SOL_87_SHARES = 2
DOGE_87_THRESHOLD_SECONDS = 180
DOGE_87_MIN_PRICE = 0.88
DOGE_87_SHARES = 2
BNB_87_THRESHOLD_SECONDS = 180
BNB_87_MIN_PRICE = 0.88
BNB_87_SHARES = 2
BCH_87_THRESHOLD_SECONDS = 180
BCH_87_MIN_PRICE = 0.88
BCH_87_SHARES = 2
ETH_87_THRESHOLD_SECONDS = 180
ETH_87_MIN_PRICE = 0.88
ETH_87_SHARES = 2
HYPE_87_THRESHOLD_SECONDS = 180
HYPE_87_MIN_PRICE = 0.88
HYPE_87_SHARES = 2
LATCH2_90_THRESHOLD_SECONDS = 240
LATCH2_90_MIN_PRICE = 0.90
LATCH2_90_SHARES = 1
TRIPLE_90_THRESHOLD_SECONDS = 300
TRIPLE_90_MIN_PRICE = 0.90
TRIPLE_90_SHARES = 1
CROSS_CONFIRM_MIN_PRICE = 0.70
TIGHT_TIME_THRESHOLD_SECONDS = 105
TIGHT_TIME_MIN_PRICE = 0.75
MIN_EDGE_FOR_87_TIERS = 0.5
EDGE_FILTER_ENABLED = False
EARLY_60_THRESHOLD_SECONDS = 800
EARLY_60_MIN_PRICE = 0.58
EARLY_60_MAX_PRICE = 0.62
EARLY_60_SHARES = 1
TRIAL_SHARES_PER_TRADE = 2
ASK_MAX_PRICE = 0.99
COIN_SIZE_MULTIPLIER = {}
# Commodities (gold, silver, oil, ...) and FX (EUR/USD, GBP/USD, USD/JPY)
# 15-min markets: same rule as the crypto 87-tiers, but price-only
# (there is no vol model for these).
CFX_87_THRESHOLD_SECONDS = 300
CFX_87_MIN_PRICE = 0.83     # entry when side probability is STRICTLY above this
CFX_87_SHARES = 2
# "Bid on anything" tier for crypto: t < 300s, side price > 0.83,
# model probability for that side > 0.83, and edge for that side > 1.5pp.
ANY_THRESHOLD_SECONDS = 300
ANY_MIN_PRICE = 0.83
ANY_MIN_MODEL_PROB = 0.83
ANY_MIN_EDGE_PP = 1.5
ANY_SHARES = 2
GENERIC_ENTRY_MIN_PRICE = 0.97
GENERIC_ENTRY_MAX_PRICE = 0.99
GENERIC_SHARES = 1
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
        ticker=ticker, client_order_id=str(uuid.uuid4()),
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
        ticker=ticker, client_order_id=str(uuid.uuid4()),
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


def place_exit(client: KalshiClient, ticker: str, close_side: str, count: float, dry_run: bool):
    price = 0.01 if close_side == "ask" else 0.99
    if close_side == "bid":
        real_side_desc = f"closing to YES @ up to ${price:.2f} (guaranteed-fill price, not the real fill)"
    else:
        real_side_desc = f"closing to NO @ down to ${1 - price:.2f} (raw order: sell YES @ ${price:.2f}, guaranteed-fill price, not the real fill)"
    print(f"{'EXIT DRY-RUN' if dry_run else 'EXITING'} {close_side.upper()} {ticker} x{count} -- {real_side_desc}")
    if dry_run:
        return {"dry_run": True}
    return client.create_order(
        ticker=ticker, client_order_id=str(uuid.uuid4()),
        side=close_side, count=str(count), price=f"{price:.2f}",
    )


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
    return None


HARD_BACKSTOP_FRACTION = 0.85


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
            if ticker in _permanently_unexitable_tickers:
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
            market = client.get_market(ticker).get("market", {})
            if market.get("status") == "finalized":
                continue
            yes_ask = market.get("yes_ask_dollars")
            yes_bid = market.get("yes_bid_dollars")
            if yes_ask is None or yes_bid is None:
                continue
            market_price = (float(yes_ask) + float(yes_bid)) / 2
            current_value = market_price if side == "bid" else (1 - market_price)
            real_bet_dollars = real_entry_price * count
            real_current_dollars = current_value * count
            threshold_dollars = real_bet_dollars * HARD_BACKSTOP_FRACTION
            if real_current_dollars > threshold_dollars + 1e-9:
                continue
            print(f"HARD BACKSTOP TRIGGERED: {ticker} real value ${real_current_dollars:.2f} fell to/below "
                  f"{HARD_BACKSTOP_FRACTION*100:.0f}% of real bet ${real_bet_dollars:.2f} "
                  f"(threshold ${threshold_dollars:.2f}) -- forcing an emergency exit, independent of "
                  f"the primary stop-loss system.")
            close_side = "ask" if side == "bid" else "bid"
            try:
                place_exit(client, ticker, close_side, count, dry_run=False)
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
            if ticker in state:
                del state[ticker]
        except Exception as e:
            print(f"HARD BACKSTOP: error checking {p.get('ticker', '?')} ({e}) -- skipping this one, "
                  f"continuing to check other real positions.")
            continue
    return triggered_pnls


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
                  f"looks unreasonable, not adopting rather than risk a broken stop-loss. "
                  f"RAW FIELDS: position_fp={p.get('position_fp')!r}, position={p.get('position')!r}, "
                  f"market_exposure_dollars={p.get('market_exposure_dollars')!r}, "
                  f"computed count={count}, computed exposure={exposure}")
            continue
        side = "bid" if position_fp > 0 else "ask"
        state[ticker] = {"count": count, "entry_price": entry_price, "side": side,
                          "dry_run": False, "peak_gain_per_contract": 0.0,
                          "entry_time": time.time(), "manually_adopted": True}
        print(f"  ADOPTED manual position: {ticker} {side.upper()} x{count:.0f} @ ~${entry_price:.2f} "
              f"(derived from Kalshi's real position data) -- trailing stop-loss now protecting it.")


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
                market = client.get_market(ticker).get("market", {})
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
            decision = check_exit(position, result.market_price, getattr(result, "model_prob", 0.5), result.seconds_remaining,
                                   reversion_z=reversion_signal.z_score, partial_profit_fraction=None,
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
                          f"check_settlements() to pick up the real result once Kalshi finishes settling it.")
                else:
                    print(f"Exit order for {ticker} failed ({e}) -- leaving it tracked, will retry next loop.")
                continue
            if position_is_real and EXIT_DRY_RUN:
                print(f"  (real position -- EXIT_DRY_RUN means no real close order was sent)")
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
        except Exception as e:
            print(f"STOP-LOSS CHECK FAILED for {ticker} ({e}) -- this position was NOT checked this "
                  f"cycle, but every other open position still was (Layer 1 isolation).")
            continue
    if stop_loss_occurred_this_cycle:
        _stop_loss_count[0] += 1
        print(f"Stop-loss count this session: {_stop_loss_count[0]}")
    if realized_pnl:
        save_json(SETTLEMENTS_FILE, settlements)
    if _stop_loss_count[0] >= 2:
        save_json(STATE_FILE, state)
        print("\n" + "=" * 60)
        print(f"STOP LOSS COUNT ({_stop_loss_count[0]}) REACHED 2 -- SHUTTING DOWN PER CIRCUIT BREAKER.")
        print("=" * 60)
        try:
            from email_alert import send_email_alert
            send_email_alert("Pulse circuit breaker tripped -- bot stopped",
                              f"Stop-loss count reached {_stop_loss_count[0]}.")
        except Exception as e:
            print(f"Email alert attempt failed ({e}) -- continuing with shutdown regardless.")
        sys.exit(1)
    return realized_pnl


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


def exit_protection_loop(client: KalshiClient, state: dict, excluded_tickers: set,
                          bankroll_and_breaker: dict, stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            with STATE_LOCK:
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
        except SystemExit:
            raise
        except Exception as e:
            print(f"EXIT PROTECTION THREAD: unexpected error ({e}) -- thread keeps running, retrying next cycle.")
        stop_event.wait(EXIT_CHECK_POLL_SECONDS)


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
    print("=" * 60)
    print("BOT VERSION MARKER: 2026-08-31-TOTAL-CAPITAL-CAP-v1")
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
                if (up_price > ANY_MIN_PRICE and result.model_prob > ANY_MIN_MODEL_PROB
                        and result.edge_pct > ANY_MIN_EDGE_PP):
                    decision = ("bid", up_price)
                    any_tier_tickers.add(result.ticker)
                elif (down_price > ANY_MIN_PRICE and (1 - result.model_prob) > ANY_MIN_MODEL_PROB
                        and (-result.edge_pct) > ANY_MIN_EDGE_PP):
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

        for _kind, _markets in (("commodity", discover_commodity_markets(client)),
                                ("fx", discover_fx_markets(client))):
            for market in _markets:
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
                if result.ticker in state or result.ticker in excluded_tickers:
                    continue
                if len(state) >= RISK_PARAMS.max_open_positions:
                    continue
                if not (9 <= result.seconds_remaining < CFX_87_THRESHOLD_SECONDS):
                    continue
                up_price = result.market_price
                down_price = 1 - result.market_price
                decision = None
                if up_price > CFX_87_MIN_PRICE:
                    decision = ("bid", up_price)
                elif down_price > CFX_87_MIN_PRICE:
                    decision = ("ask", down_price)
                if decision is None:
                    continue
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
            if real_fill_price > 0.95:
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
            this_trade_cost = trade_price * count
            available_for_new_trade = live_balance_for_cap - TOTAL_CAPITAL_SAFETY_MARGIN_DOLLARS - already_committed_dollars
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
                    if attempt < ORDER_RETRY_ATTEMPTS:
                        print(f"Entry order for {result.ticker} failed on attempt {attempt} ({e}) -- retrying...")
                        time.sleep(ORDER_RETRY_DELAY_SECONDS)
            if not order_succeeded:
                if result.ticker not in excluded_tickers:
                    print(f"Entry order for {result.ticker} failed after {ORDER_RETRY_ATTEMPTS} attempts ({last_error}) -- skipping.")
                continue
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
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
