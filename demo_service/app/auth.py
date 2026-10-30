"""Authentication: password hashing, bearer sessions, the current-user dependency (R1)."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import Depends, Header, HTTPException

from app import db
from app.models import CurrentUser
from app.settings import get_settings

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, _, expected = stored.partition("$")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return hmac.compare_digest(digest.hex(), expected)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + get_settings().session_ttl_seconds
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash_token(token), user_id, expires_at),
    )
    return token


def get_current_user(authorization: Optional[str] = Header(default=None)) -> CurrentUser:
    """FastAPI dependency: resolves the bearer token to a user or raises 401."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    row = db.execute(
        "SELECT u.id, u.email, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires_at > ?",
        (_hash_token(token), int(time.time())),
    ).one()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return CurrentUser(id=row["id"], email=row["email"], is_admin=bool(row["is_admin"]))


def require_owner(owner_id: int, user: CurrentUser) -> None:
    """Ownership helper: only the owning user (or an admin) may touch the resource."""
    if user.id != owner_id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
