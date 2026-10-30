from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import auth
from app.logging_setup import get_logger
from app.models import CurrentUser, Invoice, OrderCreate, OrderOut
from app.services import inventory, invoices, order_state, orders, pricing

logger = get_logger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=201)
def create_order(body: OrderCreate, user: CurrentUser = Depends(auth.get_current_user)):
    try:
        order_id = orders.create_order(user.id, body)
    except inventory.UnknownSku:
        raise HTTPException(status_code=404, detail="Unknown item")
    except inventory.OutOfStock:
        raise HTTPException(status_code=409, detail="Insufficient stock")
    except pricing.UnknownDiscountCode:
        raise HTTPException(status_code=400, detail="Unknown discount code")
    return orders.to_out(orders.get_order(order_id))


@router.get("", response_model=list[OrderOut])
def list_orders(user: CurrentUser = Depends(auth.get_current_user)):
    return [orders.to_out(o) for o in orders.list_orders(user.id)]


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, user: CurrentUser = Depends(auth.get_current_user)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    auth.require_owner(order["user_id"], user)
    return orders.to_out(order)


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, user: CurrentUser = Depends(auth.get_current_user)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    auth.require_owner(order["user_id"], user)
    try:
        order_state.transition(order, "cancelled")
    except order_state.InvalidTransition:
        raise HTTPException(status_code=409, detail="Order cannot be cancelled in its current state")
    return orders.to_out(order)


@router.get("/{order_id}/invoice", response_model=Invoice)
def get_invoice(order_id: int, user: CurrentUser = Depends(auth.get_current_user)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    auth.require_owner(order["user_id"], user)
    return invoices.build_invoice(order_id)
