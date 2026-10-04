import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
    PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private_key.pem")
    ENV = os.getenv("KALSHI_ENV", "demo").lower()

    @classmethod
    def base_url(cls) -> str:
        if cls.ENV == "prod":
            return "https://external-api.kalshi.com/trade-api/v2"
        return "https://external-api.demo.kalshi.co/trade-api/v2"

    @classmethod
    def validate(cls):
        if not cls.API_KEY_ID or "your-key-id" in cls.API_KEY_ID:
            raise ValueError(
                "KALSHI_API_KEY_ID is not set. Copy .env.example to .env and fill it in."
            )
        if not Path(cls.PRIVATE_KEY_PATH).exists():
            raise ValueError(
                f"Private key not found at {cls.PRIVATE_KEY_PATH}. See .env.example for how to get one."
            )
