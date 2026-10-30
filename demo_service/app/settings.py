"""Environment-based configuration. Secrets are never hardcoded (R8)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_path: str
    export_dir: str
    tax_rate: Decimal
    http_timeout_seconds: float
    session_ttl_seconds: int
    payment_gateway_url: str
    payment_gateway_api_key: str
    email_api_url: str
    email_api_key: str
    webhook_secret: str


@lru_cache
def get_settings() -> Settings:
    env = os.environ
    return Settings(
        database_path=env.get("DATABASE_PATH", "orders.db"),
        export_dir=env.get("EXPORT_DIR", "exports"),
        tax_rate=Decimal(env.get("TAX_RATE", "0.0825")),
        http_timeout_seconds=float(env.get("HTTP_TIMEOUT_SECONDS", "5")),
        session_ttl_seconds=int(env.get("SESSION_TTL_SECONDS", "86400")),
        payment_gateway_url=env.get("PAYMENT_GATEWAY_URL", "https://gateway.example.test"),
        payment_gateway_api_key=env.get("PAYMENT_GATEWAY_API_KEY", ""),
        email_api_url=env.get("EMAIL_API_URL", "https://mail.example.test"),
        email_api_key=env.get("EMAIL_API_KEY", ""),
        webhook_secret=env.get("WEBHOOK_SECRET", ""),
    )
