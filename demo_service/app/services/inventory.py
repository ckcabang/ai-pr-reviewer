"""Stock handling. Reservation is a conditional decrement inside a transaction (no oversell)."""
from __future__ import annotations

from typing import Optional

from app import db
from app.models import InventoryOut, OrderItemIn


class OutOfStock(Exception):
    def __init__(self, sku: str):
        super().__init__(f"insufficient stock for {sku}")
        self.sku = sku


class UnknownSku(LookupError):
    pass


def get_item(sku: str) -> Optional[InventoryOut]:
    row = db.execute("SELECT sku, name, price, stock FROM inventory WHERE sku = ?", (sku,)).one()
    return InventoryOut(**dict(row)) if row else None


def reserve(sku: str, quantity: int) -> None:
    """Take `quantity` units out of stock atomically; raises OutOfStock if there are not enough."""
    with db.transaction():
        result = db.execute(
            "UPDATE inventory SET stock = stock - ? WHERE sku = ? AND stock >= ?",
            (quantity, sku, quantity),
        )
        if result.rowcount == 0:
            if get_item(sku) is None:
                raise UnknownSku(sku)
            raise OutOfStock(sku)


def reserve_items(items: list[OrderItemIn]) -> None:
    """Reserve every line or none: any failure rolls back the whole transaction."""
    with db.transaction():
        for item in items:
            reserve(item.sku, item.quantity)


def restock(sku: str, quantity: int) -> InventoryOut:
    with db.transaction():
        if get_item(sku) is None:
            raise UnknownSku(sku)
        db.execute("UPDATE inventory SET stock = stock + ? WHERE sku = ?", (quantity, sku))
    return get_item(sku)
