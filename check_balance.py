"""
One-off check: confirms your real Kalshi balance directly, independent
of anything the bot itself is tracking locally.

Usage: python3 check_balance.py
"""
from kalshi_client import KalshiClient

client = KalshiClient()
balance = client.get_balance()
print(f"Real Kalshi balance: ${balance['balance'] / 100:.2f}")
