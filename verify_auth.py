"""
Run this to check ONLY whether your credentials work -- one call,
fetching your real balance. Nothing else. This is deliberately
separate from order placement, so we can tell apart "the key itself
doesn't work" from "the key works, but something about placing an
order specifically is failing."

Usage:
    python3 verify_auth.py
"""
from kalshi_client import KalshiClient
from config import Config

print(f"Testing against: {Config.base_url()}  (KALSHI_ENV={Config.ENV})")
print(f"Key ID: {Config.API_KEY_ID[:8]}...{Config.API_KEY_ID[-4:]}" if len(Config.API_KEY_ID) > 12 else "Key ID: (too short, check .env)")
print(f"Private key path: {Config.PRIVATE_KEY_PATH}")
print()

try:
    client = KalshiClient()
    balance = client.get_balance()
    dollars = balance.get("balance", 0) / 100
    print(f"SUCCESS. Real balance: ${dollars:.2f}")
    print("Authentication is working correctly for GET requests.")
    print()
    print("If order placement is STILL failing with 'user_not_found' despite this")
    print("succeeding, the problem is likely specific to the order endpoint itself --")
    print("not your credentials generally. Worth checking Kalshi's current API docs")
    print("for whether /portfolio/events/orders has different requirements than")
    print("/portfolio/balance (e.g. a required header, a different permission scope,")
    print("or an account-type restriction on trading specifically).")
except Exception as e:
    print(f"FAILED: {e}")
    print()
    print("Do not proceed until this succeeds. Common causes:")
    print("  - KALSHI_ENV in .env doesn't match where this key was actually generated")
    print("    (a demo-site key will not work against prod, and vice versa)")
    print("  - The private key file doesn't correspond to this exact Key ID")
    print("  - A stray character/typo in .env (check for accidental spaces after '=')")
    print("  - System clock is inaccurate (check: date)")
    print("  - Wrong virtual environment active (check: which python3)")
