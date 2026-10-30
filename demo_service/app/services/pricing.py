"""Order pricing. Every amount is Decimal dollars (R3); invoices and payments consume it."""
from __future__ import annotations

from decimal import Decimal

from app import db
from app.models import to_money
from app.settings import get_settings

HUNDRED = Decimal("100")


class UnknownDiscountCode(ValueError):
    pass


def get_subtotal(order_id: int) -> Decimal:
    rows = db.execute(
        "SELECT quantity, unit_price FROM order_items WHERE order_id = ?", (order_id,)
    ).all()
    return to_money(sum((Decimal(r["unit_price"]) * r["quantity"] for r in rows), Decimal("0")))


def get_discount_percent(code: str) -> Decimal:
    row = db.execute("SELECT percent_off FROM discount_codes WHERE code = ?", (code,)).one()
    if row is None:
        raise UnknownDiscountCode(code)
    return Decimal(row["percent_off"])


def apply_discount(amount: Decimal, percent: Decimal) -> Decimal:
    """Take `percent` off `amount`. The percentage is clamped so a total never goes negative."""
    percent = min(max(percent, Decimal("0")), HUNDRED)
    return to_money(amount - amount * percent / HUNDRED)


def calculate_tax(amount: Decimal, rate: Decimal | None = None) -> Decimal:
    rate = get_settings().tax_rate if rate is None else rate
    return to_money(amount * rate)


def get_order_total(order_id: int) -> Decimal:
    """Total owed for an order in dollars: subtotal, less any discount code, plus tax."""
    order = db.execute("SELECT discount_code FROM orders WHERE id = ?", (order_id,)).one()
    if order is None:
        raise LookupError(f"order {order_id} not found")
    amount = get_subtotal(order_id)
    if order["discount_code"]:
        amount = apply_discount(amount, get_discount_percent(order["discount_code"]))
    return to_money(amount + calculate_tax(amount))
