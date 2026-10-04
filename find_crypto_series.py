"""
One-time discovery script. KXBTC15M is confirmed; the ETH/SOL entries in
strategy.py's CRYPTO_SERIES_PREFIXES are a guessed naming pattern, not
verified. Run this once and check the real tickers before trusting them.

Usage: python3 find_crypto_series.py
"""
from kalshi_client import KalshiClient

CRYPTO_KEYWORDS = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto"]

client = KalshiClient()
resp = client.get_series(category="Crypto")
series_list = resp.get("series", [])

print(f"Found {len(series_list)} series under category 'Crypto':\n")
for s in series_list:
    print(f"  {s.get('ticker', ''):<20} {s.get('title', '')}")

if not series_list:
    print("(No 'Crypto' category found -- trying keyword search across 'Sports' fallback category instead.)")
    resp2 = client.get_series(category="Sports")   # some Kalshi categories overlap; harmless to also check
    for s in resp2.get("series", []):
        title = s.get("title", "").lower()
        if any(k in title for k in CRYPTO_KEYWORDS):
            print(f"  {s.get('ticker', ''):<20} {s.get('title', '')}")
