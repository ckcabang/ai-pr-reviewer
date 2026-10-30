"""Logging setup and the redaction helper (R7): log through `redact()`, never raw payloads."""
from __future__ import annotations

import logging
import re
from typing import Any

SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "authorization", "card_number", "cvv"}
REDACTED = "[REDACTED]"
_CARD_RE = re.compile(r"\b\d{13,19}\b")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def mask_card(number: str) -> str:
    """Keep only the last four digits of a card number."""
    digits = re.sub(r"\D", "", number)
    return "*" * max(len(digits) - 4, 0) + digits[-4:]


def redact(value: Any) -> Any:
    """Return a copy of `value` that is safe to log: sensitive keys masked, card numbers truncated."""
    if isinstance(value, dict):
        return {
            k: REDACTED if str(k).lower() in SENSITIVE_KEYS else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _CARD_RE.sub(lambda m: mask_card(m.group()), value)
    return value
