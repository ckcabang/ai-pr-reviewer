"""Transactional email wrapper (R4)."""
from __future__ import annotations

from typing import Optional

import httpx

from app.settings import get_settings

_transport: Optional[httpx.BaseTransport] = None  # tests swap in an httpx.MockTransport


class EmailError(Exception):
    pass


def send(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    try:
        with httpx.Client(
            base_url=settings.email_api_url,
            timeout=settings.http_timeout_seconds,
            headers={"Authorization": f"Bearer {settings.email_api_key}"},
            transport=_transport,
        ) as client:
            client.post("/send", json={"to": to, "subject": subject, "body": body}).raise_for_status()
    except httpx.HTTPError as exc:
        raise EmailError("email delivery failed") from exc
