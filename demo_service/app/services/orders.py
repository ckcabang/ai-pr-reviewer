"""Order persistence helpers shared by the order, payment, refund and export routes."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app import db
from app.models import OrderCreate, OrderItemOut, OrderOut
from app.services import inventory, pricing


def create_order(user_id: int, payload: OrderCreate) -> int:
    """Create a pending order and reserve its stock in one transaction."""
    with db.transaction():
        if payload.discount_code:
            pricing.get_discount_percent(payload.discount_code)  # fail early on unknown codes
        prices = {}
        for item in payload.items:
            stocked = inventory.get_item(item.sku)
            if stocked is None:
                raise inventory.UnknownSku(item.sku)
            prices[item.sku] = stocked.price
        inventory.reserve_items(payload.items)
        order_id = db.execute(
            "INSERT INTO orders (user_id, discount_code) VALUES (?, ?)",
            (user_id, payload.discount_code),
        ).lastrowid
        for item in payload.items:
            db.execute(
                "INSERT INTO order_items (order_id, sku, quantity, unit_price) VALUES (?, ?, ?, ?)",
                (order_id, item.sku, item.quantity, str(prices[item.sku])),
            )
    return order_id


def get_order(order_id: int) -> Optional[dict]:
    row = db.execute(
        "SELECT id, user_id, status, discount_code FROM orders WHERE id = ?", (order_id,)
    ).one()
    return dict(row) if row else None


def list_orders(user_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT id, user_id, status, discount_code FROM orders WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).all()
    return [dict(r) for r in rows]


def get_items(order_id: int) -> list[OrderItemOut]:
    rows = db.execute(
        "SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)
    ).all()
    return [OrderItemOut(sku=r["sku"], quantity=r["quantity"], unit_price=Decimal(r["unit_price"])) for r in rows]


def to_out(order: dict) -> OrderOut:
    return OrderOut(
        id=order["id"],
        user_id=order["user_id"],
        status=order["status"],
        total=pricing.get_order_total(order["id"]),
        items=get_items(order["id"]),
    )
