from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import auth
from app.models import CurrentUser, InventoryOut, RestockRequest
from app.services import inventory

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/{sku}", response_model=InventoryOut)
def get_stock(sku: str, user: CurrentUser = Depends(auth.get_current_user)):
    item = inventory.get_item(sku)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item")
    return item


@router.post("/{sku}/restock", response_model=InventoryOut)
def restock(sku: str, body: RestockRequest, admin: CurrentUser = Depends(auth.require_admin)):
    try:
        return inventory.restock(sku, body.quantity)
    except inventory.UnknownSku:
        raise HTTPException(status_code=404, detail="Unknown item")
