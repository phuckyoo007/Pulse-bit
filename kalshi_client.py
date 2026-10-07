"""
Thin Kalshi REST client: RSA-PSS request signing plus wrapper methods for
the endpoints this bot needs.
"""
import base64
import os
import time
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from config import Config


class KalshiClient:
    def __init__(self):
        # Key can come from the KALSHI_PRIVATE_KEY env var (the PEM text
        # itself) -- no file needed. Falls back to the .pem file path.
        pem_text = os.getenv("KALSHI_PRIVATE_KEY", "").strip()
        try:
            Config.validate()
        except Exception as e:
            if not pem_text:
                raise
            print(f"Config.validate() complained ({e}) -- continuing because KALSHI_PRIVATE_KEY is set.")
        self.base_url = Config.base_url()
        if pem_text:
            pem_text = pem_text.strip('"').strip("'").replace("\\n", "\n")
            pem_bytes = pem_text.encode("utf-8")
        else:
            with open(Config.PRIVATE_KEY_PATH, "rb") as f:
                pem_bytes = f.read()
        self.private_key = serialization.load_pem_private_key(pem_bytes, password=None)
        if not isinstance(self.private_key, rsa.RSAPrivateKey):
            raise ValueError(
                f"The private key loaded is a {type(self.private_key).__name__}, not an RSA key. "
                f"Kalshi API keys are RSA -- this is the wrong key (probably from another service). "
                f"Use the .pem Kalshi gave you when you created the API key."
            )

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

    def get_balance_by_shard(self):
        """Returns {exchange_index: dollars} from /portfolio/balance's balance_breakdown."""
        resp = self.get_balance()
        out = {}
        for row in resp.get("balance_breakdown", []) or []:
            out[int(row["exchange_index"])] = float(row["balance"])
        return out

    def transfer_between_shards(self, source_shard: int, dest_shard: int, dollars: float):
        """Moves cash between exchange shards inside the same account (amount is in centicents)."""
        body = {"source": "event_contract", "destination": "event_contract",
                "source_exchange_shard": int(source_shard), "destination_exchange_shard": int(dest_shard),
                "amount": int(round(dollars * 10000))}
        return self._request("POST", "/portfolio/intra_exchange_instance_transfer", json_body=body)

    def get_market(self, ticker: str):
        return self._request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str):
        return self._request("GET", f"/markets/{ticker}/orderbook")

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
        price always denominated in YES terms, even for 'ask' orders --
        confirmed via Kalshi's own official create-order-v2 docs, the
        exact page their deprecated_v1_order_endpoint error pointed to.
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
        print(f"  [RAW ORDER BODY]: {body}")
        return self._request("POST", "/portfolio/events/orders", json_body=body)

    def cancel_order(self, order_id: str):
        return self._request("DELETE", f"/portfolio/events/orders/{order_id}")
