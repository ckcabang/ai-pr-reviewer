from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, Header, HTTPException, Request

from app.logging_setup import get_logger, redact
from app.services import order_state, orders
from app.settings import get_settings

logger = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

SHIPPING_STATES = {"shipped", "delivered"}


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@router.post("/shipping")
async def shipping_update(request: Request, x_signature: str = Header(default="")):
    """Carrier callback: moves an order to shipped or delivered."""
    raw = await request.body()
    expected = sign(raw, get_settings().webhook_secret)
    if not hmac.compare_digest(expected, x_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw)
        logger.info("shipping webhook %s", redact(payload))
        if payload["status"] not in SHIPPING_STATES:
            raise HTTPException(status_code=422, detail="Unsupported status")
        order = orders.get_order(int(payload["order_id"]))
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        order_state.transition(order, payload["status"])
    except HTTPException:
        raise
    except order_state.InvalidTransition:
        raise HTTPException(status_code=409, detail="Order cannot move to that state")
    except Exception:
        logger.exception("shipping webhook failed")
        raise HTTPException(status_code=400, detail="Could not process webhook")
    return {"order_id": order["id"], "status": order["status"]}
