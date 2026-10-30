"""Order lifecycle. Status changes go only through `transition()` (R9)."""
from __future__ import annotations

from typing import MutableMapping

from app import db

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"paid", "cancelled"},
    "paid": {"shipped", "refunded", "cancelled"},
    "shipped": {"delivered", "refunded"},
    "delivered": {"refunded"},
    "cancelled": set(),
    "refunded": set(),
}


class InvalidTransition(ValueError):
    def __init__(self, current: str, new: str):
        super().__init__(f"cannot move order from {current!r} to {new!r}")
        self.current = current
        self.new = new


def is_allowed(current: str, new: str) -> bool:
    return new in ALLOWED_TRANSITIONS.get(current, set())


def transition(order: MutableMapping, new_state: str) -> None:
    """Persist a status change and update `order` in place; raises InvalidTransition if illegal."""
    if not is_allowed(order["status"], new_state):
        raise InvalidTransition(order["status"], new_state)
    db.execute("UPDATE orders SET status = ? WHERE id = ?", (new_state, order["id"]))
    order["status"] = new_state
