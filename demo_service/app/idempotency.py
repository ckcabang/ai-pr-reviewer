"""Idempotency for money-moving endpoints (R5): capture and refund call `check_and_store`."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from app import db


class IdempotencyConflict(Exception):
    """The key was reused with a different request, or the first request is still running."""


def hash_request(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def check_and_store(key: str, request_hash: str) -> Optional[dict[str, Any]]:
    """Reserve `key` for this request.

    Returns None when the key is new (the caller should do the work and then call
    `save_response`), or the saved response when the same request was already completed.
    """
    with db.transaction():
        row = db.execute(
            "SELECT request_hash, response FROM idempotency_keys WHERE key = ?", (key,)
        ).one()
        if row is None:
            db.execute(
                "INSERT INTO idempotency_keys (key, request_hash) VALUES (?, ?)", (key, request_hash)
            )
            return None
    if row["request_hash"] != request_hash:
        raise IdempotencyConflict("Idempotency-Key was already used for a different request")
    if row["response"] is None:
        raise IdempotencyConflict("A request with this Idempotency-Key is still in progress")
    return json.loads(row["response"])


def save_response(key: str, response: dict[str, Any]) -> None:
    db.execute("UPDATE idempotency_keys SET response = ? WHERE key = ?", (json.dumps(response), key))


def release(key: str) -> None:
    """Forget a key whose request failed, so the client can retry with the same key."""
    db.execute("DELETE FROM idempotency_keys WHERE key = ? AND response IS NULL", (key,))
