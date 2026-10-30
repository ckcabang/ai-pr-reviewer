"""Invoices. The amount due comes from `pricing.get_order_total()` (Decimal dollars)."""
from __future__ import annotations

from app.models import Invoice
from app.services import orders, pricing


def format_dollars(amount) -> str:
    return f"${amount:,.2f}"


def build_invoice(order_id: int) -> Invoice:
    amount_due = pricing.get_order_total(order_id)
    lines = [
        f"{item.quantity} x {item.sku} @ {format_dollars(item.unit_price)}"
        for item in orders.get_items(order_id)
    ]
    lines.append(f"Subtotal before discount and tax: {format_dollars(pricing.get_subtotal(order_id))}")
    return Invoice(
        order_id=order_id,
        lines=lines,
        amount_due=amount_due,
        display_total=format_dollars(amount_due),
    )
