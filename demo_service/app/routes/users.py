from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app import auth, db
from app.logging_setup import get_logger
from app.models import CurrentUser, LoginRequest, UserCreate, UserOut, UserUpdate

logger = get_logger(__name__)
router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserOut, status_code=201)
def register(body: UserCreate):
    try:
        cur = db.execute(
            "INSERT INTO users (email, full_name, password_hash) VALUES (?, ?, ?)",
            (body.email, body.full_name, auth.hash_password(body.password)),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Email already registered")
    return UserOut(id=cur.lastrowid, email=body.email, full_name=body.full_name)


@router.post("/login")
def login(body: LoginRequest):
    row = db.execute("SELECT id, password_hash FROM users WHERE email = ?", (body.email,)).one()
    if row is None or not auth.verify_password(body.password, row["password_hash"]):
        logger.warning("failed login for %s", body.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": auth.create_session(row["id"])}


def _load_user(user_id: int) -> UserOut:
    row = db.execute(
        "SELECT id, email, full_name, shipping_address FROM users WHERE id = ?", (user_id,)
    ).one()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut(**dict(row))


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, user: CurrentUser = Depends(auth.get_current_user)):
    auth.require_owner(user_id, user)
    return _load_user(user_id)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, user: CurrentUser = Depends(auth.get_current_user)):
    auth.require_owner(user_id, user)
    if body.full_name is not None:
        db.execute("UPDATE users SET full_name = ? WHERE id = ?", (body.full_name, user_id))
    return _load_user(user_id)
