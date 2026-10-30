"""Pydantic models. Money is Decimal dollars everywhere (R3)."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from pydantic import BaseModel, Field

CENT = Decimal("0.01")


def to_money(value: Decimal | str | int) -> Decimal:
    """Quantize to whole cents. Never accepts float: floats would carry binary rounding error."""
    if isinstance(value, float):
        raise TypeError("money must be Decimal, not float")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class CurrentUser(BaseModel):
    id: int
    email: str
    is_admin: bool = False


class UserCreate(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    full_name: str
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserUpdate(BaseModel):
    full_name: Optional[str] = None


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    shipping_address: Optional[str] = None


class OrderItemIn(BaseModel):
    sku: str
    quantity: int = Field(gt=0, le=100)


class OrderCreate(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1)
    discount_code: Optional[str] = None


class OrderItemOut(BaseModel):
    sku: str
    quantity: int
    unit_price: Decimal


class OrderOut(BaseModel):
    id: int
    user_id: int
    status: str
    total: Decimal
    items: list[OrderItemOut]


class CaptureRequest(BaseModel):
    order_id: int


class RefundRequest(BaseModel):
    order_id: int


class InventoryOut(BaseModel):
    sku: str
    name: str
    price: Decimal
    stock: int


class RestockRequest(BaseModel):
    quantity: int = Field(gt=0)


class Invoice(BaseModel):
    order_id: int
    lines: list[str]
    amount_due: Decimal
    display_total: str
