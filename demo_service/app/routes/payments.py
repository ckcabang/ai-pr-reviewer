from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app import auth, db, idempotency
from app.clients import payment_gateway
from app.logging_setup import get_logger, redact
from app.models import CaptureRequest, CurrentUser
from app.services import order_state, orders, pricing

logger = get_logger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/capture")
def capture_payment(
    body: CaptureRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(auth.get_current_user),
):
    order = orders.get_order(body.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    auth.require_owner(order["user_id"], user)

    request_hash = idempotency.hash_request({"order_id": order["id"], "user_id": user.id})
    try:
        saved = idempotency.check_and_store(idempotency_key, request_hash)
    except idempotency.IdempotencyConflict:
        raise HTTPException(status_code=409, detail="Idempotency-Key conflict")
    if saved is not None:
        return saved
    if not order_state.is_allowed(order["status"], "paid"):
        idempotency.release(idempotency_key)
        raise HTTPException(status_code=409, detail="Order cannot be paid in its current state")

    try:
        amount = pricing.get_order_total(order["id"])
        logger.info("capturing payment %s", redact({"order_id": order["id"], "amount": str(amount)}))
        charge = payment_gateway.capture(order["id"], amount)
        with db.transaction():
            db.execute(
                "INSERT INTO payments (order_id, charge_id, amount) VALUES (?, ?, ?)",
                (order["id"], charge["id"], str(amount)),
            )
            order_state.transition(order, "paid")
        response = {"order_id": order["id"], "charge_id": charge["id"], "amount": str(amount), "status": "paid"}
        idempotency.save_response(idempotency_key, response)
        return response
    except Exception:
        idempotency.release(idempotency_key)
        logger.exception("payment capture failed for order %s", order["id"])
        raise HTTPException(status_code=502, detail="Payment could not be processed")
