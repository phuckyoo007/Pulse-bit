"""
One-time check for the 7 newly-added coins. Confirms each one's price
feed actually returns a real spot price and volatility estimate before
you trust it live -- same lesson as every ticker guess in this project:
verify before running real money through it, don't assume.

Usage: python3 verify_new_coins.py
"""
from price_feed import get_spot_and_vol, COINBASE_PRODUCT_MAP, COINGECKO_ID_MAP

NEW_COINS = ["ADA", "DOGE", "BCH", "ZEC", "HYPE", "NEAR", "TON"]

print("Checking each new coin's price feed...\n")
for coin in NEW_COINS:
    source = "Coinbase" if coin in COINBASE_PRODUCT_MAP else ("CoinGecko" if coin in COINGECKO_ID_MAP else "NONE CONFIGURED")
    spot, vol = get_spot_and_vol(coin)
    if spot is None or vol is None:
        print(f"  {coin:<6} ({source}): FAILED -- spot={spot}, vol={vol}. Check the product/coin ID is correct.")
    else:
        print(f"  {coin:<6} ({source}): OK -- spot=${spot:,.2f}, annualized vol={vol:.2f}")

print(
    "\nAny coin marked FAILED shouldn't be trusted live yet -- check its product ID in "
    "price_feed.py's COINBASE_PRODUCT_MAP/COINGECKO_ID_MAP, or remove it from "
    "CRYPTO_SERIES_PREFIXES in strategy.py until fixed.\n"
    "\nFor NEAR and TON specifically: if they came back OK here, try moving them from "
    "COINGECKO_ID_MAP to COINBASE_PRODUCT_MAP (as e.g. 'NEAR-USD', 'TON-USD') and re-run this "
    "-- if Coinbase's 1-minute data works for them too, that's a better volatility estimate "
    "than CoinGecko's coarser ~5-minute data."
)
