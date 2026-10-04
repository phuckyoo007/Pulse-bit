"""
Standalone market watcher -- for watching Kalshi crypto/commodity/index
markets live WHILE YOU BID MANUALLY, completely separate from Pulse.

READ-ONLY. This file contains NO order-placement code of any kind --
it never calls client.create_order() or anything that could place a
real bet. It only reads market data and prints it. Safe to run
alongside Pulse itself; the two don't interact or share any state.

Reuses the exact same market-discovery and price/model logic as
Pulse's strategy.py, so what you see here matches what Pulse itself
is seeing -- just displayed for a human to read and act on manually,
instead of feeding an automated entry decision.

USAGE:
    python3 market_watch.py

Refreshes every REFRESH_SECONDS. Ctrl+C to stop -- this never blocks
on anything that needs a clean shutdown, so it's safe to just kill it.
"""
import time
from datetime import datetime, timezone
from kalshi_client import KalshiClient
from strategy import (
    discover_crypto_markets, evaluate_crypto_market,
    discover_commodity_markets, evaluate_commodity_market,
    discover_index_markets, evaluate_index_market,
)

REFRESH_SECONDS = 2

# Only show markets inside this time window, so the screen doesn't
# fill with hours-out markets you can't act on yet. Adjust freely --
# this is just a display filter, it doesn't affect what's fetched.
MAX_SECONDS_REMAINING_TO_SHOW = 900


def format_row(coin_or_category, title, up_price, down_price, seconds_remaining,
                model_prob=None, edge_pct=None):
    time_str = f"t-{int(seconds_remaining)}s" if seconds_remaining >= 0 else "CLOSED"
    base = f"  [{coin_or_category:<8}] {title[:42]:<42} up=${up_price:.2f} down=${down_price:.2f} {time_str:>8}"
    if model_prob is not None:
        base += f"  model={model_prob:.2f} edge={edge_pct:+.1f}pp"
    return base


def run():
    client = KalshiClient()
    print("Market watcher started -- READ ONLY, places no orders. Ctrl+C to stop.\n")
    while True:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"\n--- {now_str} " + "-" * 60)

        # Crypto (has the volatility model, so shows model/edge too)
        try:
            crypto_markets = discover_crypto_markets(client)
        except Exception as e:
            print(f"  Crypto market fetch failed: {e}")
            crypto_markets = []
        rows = []
        for market in crypto_markets:
            result = evaluate_crypto_market(market)
            if result is None:
                continue
            if not (0 <= result.seconds_remaining <= MAX_SECONDS_REMAINING_TO_SHOW):
                continue
            rows.append((result.seconds_remaining, format_row(
                result.coin, result.title, result.market_price, 1 - result.market_price,
                result.seconds_remaining, result.model_prob, result.edge_pct,
            )))

        # Commodities (no model -- pure price/time, same as Pulse treats them)
        try:
            commodity_markets = discover_commodity_markets(client)
        except Exception as e:
            print(f"  Commodity market fetch failed: {e}")
            commodity_markets = []
        for market in commodity_markets:
            result = evaluate_commodity_market(market)
            if result is None:
                continue
            if not (0 <= result.seconds_remaining <= MAX_SECONDS_REMAINING_TO_SHOW):
                continue
            rows.append((result.seconds_remaining, format_row(
                result.commodity, result.title, result.market_price, 1 - result.market_price,
                result.seconds_remaining,
            )))

        # Indices (same as commodities -- no model)
        try:
            index_markets = discover_index_markets(client)
        except Exception as e:
            print(f"  Index market fetch failed: {e}")
            index_markets = []
        for market in index_markets:
            result = evaluate_index_market(market)
            if result is None:
                continue
            if not (0 <= result.seconds_remaining <= MAX_SECONDS_REMAINING_TO_SHOW):
                continue
            rows.append((result.seconds_remaining, format_row(
                result.index, result.title, result.market_price, 1 - result.market_price,
                result.seconds_remaining,
            )))

        if not rows:
            print(f"  No markets currently within {MAX_SECONDS_REMAINING_TO_SHOW}s of close.")
        else:
            # Soonest-to-close first, since that's usually what you care about most
            rows.sort(key=lambda r: r[0])
            for _, line in rows:
                print(line)

        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nStopped.")
