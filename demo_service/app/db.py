"""SQLite access. All SQL goes through `execute(sql, params)` with bound parameters (R2)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from app.settings import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    shipping_address TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    price TEXT NOT NULL,
    stock INTEGER NOT NULL CHECK (stock >= 0)
);
CREATE TABLE IF NOT EXISTS discount_codes (
    code TEXT PRIMARY KEY,
    percent_off TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending',
    discount_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    charge_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    refund_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    response TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_active_conn: ContextVar[sqlite3.Connection | None] = ContextVar("active_conn", default=None)


@dataclass
class Result:
    rows: list[sqlite3.Row] = field(default_factory=list)
    rowcount: int = 0
    lastrowid: int | None = None

    def one(self) -> sqlite3.Row | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[sqlite3.Row]:
        return self.rows


def get_conn() -> sqlite3.Connection:
    # isolation_level=None: autocommit, so transactions are explicit via `transaction()`.
    conn = sqlite3.connect(get_settings().database_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def execute(sql: str, params: Sequence[Any] = ()) -> Result:
    """Run one statement with bound parameters. Joins the open transaction if there is one."""
    conn = _active_conn.get()
    owns_conn = conn is None
    if conn is None:
        conn = get_conn()
    try:
        cur = conn.execute(sql, tuple(params))
        return Result(rows=cur.fetchall(), rowcount=cur.rowcount, lastrowid=cur.lastrowid)
    finally:
        if owns_conn:
            conn.close()


@contextmanager
def transaction() -> Iterator[None]:
    """Run the enclosed `execute` calls atomically. Nested use joins the outer transaction."""
    if _active_conn.get() is not None:
        yield
        return
    conn = get_conn()
    token = _active_conn.set(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        _active_conn.reset(token)
        conn.close()
