"""
Thin Kalshi REST client: RSA-PSS request signing plus wrapper methods for
the endpoints this bot needs.

FIXED, per real evidence of repeated market_not_found/user_not_found
errors on order placement: create_order() was using an OUTDATED
endpoint and schema (/portfolio/events/orders, with side="bid"/"ask"
and a decimal-string price). Current (2026) documentation confirms
the real, current endpoint and schema is:

    POST /portfolio/orders
    {
        "ticker": ...,
        "action": "buy" or "sell",
        "side": "yes" or "no",
        "type": "limit",
        "count": <int>,
        "yes_price": <int, PRICE IN CENTS, not a decimal string>,
        "client_order_id": ...
    }

This matches the original kalshi_client.py's own docstring note that
Kalshi's order API was "mid-transition as of 2026" -- this fix
reflects the transition having completed. bot.py's callers still pass
"bid"/"ask" as their side parameter (matching the rest of this
project's terminology); create_order() below translates that into
the real action/side pair internally, so bot.py itself doesn't need
to change.
"""
import base64
import time
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from config import Config


class KalshiClient:
    def __init__(self):
        Config.validate()
        self.base_url = Config.base_url()
        with open(Config.PRIVATE_KEY_PATH, "rb") as f:
            self.private_key = serialization.load_pem_private_key(f.read(), password=None)

    def _sign(self, method: str, path: str) -> dict:
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method}{path}".encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": Config.API_KEY_ID,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, params=None, json_body=None):
        url = self.base_url + path
        sign_path = urlparse(url).path
        headers = self._sign(method, sign_path)
        resp = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=15)
        if not resp.ok:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise requests.exceptions.HTTPError(
                f"{resp.status_code} error from Kalshi on {method} {path}: {detail}"
            )
        return resp.json()

    # --- Market data (these work unauthenticated too, but signed headers are harmless) ---
    def get_markets(self, status="open", series_ticker=None, event_ticker=None, limit=100, cursor=None):
        params = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/markets", params=params)

    def get_series(self, category: str = None, tags: str = None, limit: int = 200):
        params = {"limit": limit}
        if category:
            params["category"] = category
        if tags:
            params["tags"] = tags
        return self._request("GET", "/series", params=params)

    def get_market(self, ticker: str):
        return self._request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str):
        return self._request("GET", f"/markets/{ticker}/orderbook")

    # --- Portfolio (require real auth) ---
    def get_balance(self):
        return self._request("GET", "/portfolio/balance")

    def get_deposits(self, limit: int = 100):
        return self._request("GET", "/portfolio/deposits", params={"limit": limit})

    def get_fills(self, limit: int = 100, cursor: str = None):
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/portfolio/fills", params=params)

    def get_positions(self):
        return self._request("GET", "/portfolio/positions")

    def create_order(self, ticker: str, client_order_id: str, side: str, count: str, price: str,
                      time_in_force: str = "good_till_canceled"):
        """side: 'bid' (buy yes) or 'ask' (sell yes / economically long no).

        REVERTED, per Kalshi's own error response confirming the real,
        current endpoint: my earlier "fix" to /portfolio/orders with a
        separate action/yes-no split was WRONG -- based on a stale
        search result, not Kalshi's actual current docs. The real
        current endpoint (confirmed via the exact URL Kalshi's own
        deprecated_v1_order_endpoint error pointed to) is
        /portfolio/events/orders, with side=bid/ask directly and price
        always denominated in YES terms -- exactly what this client
        originally did before that incorrect "fix". time_in_force and
        self_trade_prevention_type are both REQUIRED fields per the
        confirmed schema.
        """
        body = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": side,
            "count": count,
            "price": price,
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
        }
        # DEBUG, per explicit request -- print the EXACT raw request
        # body being sent, to rule out any formatting/conversion
        # discrepancy between what bot.py's own summary print claims
        # and what's actually transmitted to Kalshi.
        print(f"  [RAW ORDER BODY]: {body}")
        return self._request("POST", "/portfolio/events/orders", json_body=body)

    def cancel_order(self, order_id: str):
        return self._request("DELETE", f"/portfolio/events/orders/{order_id}")
