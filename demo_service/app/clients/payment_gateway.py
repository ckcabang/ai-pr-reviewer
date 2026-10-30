"""Payment gateway wrapper (R4). Every call has a timeout; only idempotent calls are retried."""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Optional

import httpx

from app.logging_setup import get_logger
from app.settings import get_settings

logger = get_logger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 0.2
_transport: Optional[httpx.BaseTransport] = None  # tests swap in an httpx.MockTransport


class GatewayError(Exception):
    pass


def _client() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        base_url=settings.payment_gateway_url,
        timeout=settings.http_timeout_seconds,
        headers={"Authorization": f"Bearer {settings.payment_gateway_api_key}"},
        transport=_transport,
    )


def _request(method: str, path: str, *, json: Optional[dict] = None, retry: bool = False) -> dict[str, Any]:
    """Send one request. `retry=True` is only for idempotent calls (reads)."""
    attempts = MAX_ATTEMPTS if retry else 1
    for attempt in range(1, attempts + 1):
        try:
            with _client() as client:
                response = client.request(method, path, json=json)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("gateway %s %s failed (attempt %d/%d): %s", method, path, attempt, attempts, exc)
            if attempt == attempts:
                raise GatewayError(f"{method} {path} failed") from exc
            time.sleep(RETRY_DELAY_SECONDS)
    raise GatewayError("unreachable")


def get_charge(charge_id: str) -> dict[str, Any]:
    """Look up a charge. A read, so safe to retry."""
    return _request("GET", f"/charges/{charge_id}", retry=True)


def capture(order_id: int, amount: Decimal) -> dict[str, Any]:
    """Charge the customer. Not idempotent at the gateway, so never retried here."""
    return _request("POST", "/charges", json={"order_id": order_id, "amount": str(amount)})


def refund(charge_id: str, amount: Decimal) -> dict[str, Any]:
    return _request("POST", f"/charges/{charge_id}/refunds", json={"amount": str(amount)})
