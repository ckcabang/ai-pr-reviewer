from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException

from app import auth, db, idempotency
from app.clients import email_client, payment_gateway
from app.logging_setup import get_logger, redact
from app.models import CurrentUser, RefundRequest
from app.services import order_state, orders

logger = get_logger(__name__)
router = APIRouter(prefix="/refunds", tags=["refunds"])


@router.post("")
def refund_order(
    body: RefundRequest,
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
    if not order_state.is_allowed(order["status"], "refunded"):
        idempotency.release(idempotency_key)
        raise HTTPException(status_code=409, detail="Order cannot be refunded in its current state")

    payment = db.execute(
        "SELECT charge_id, amount FROM payments WHERE order_id = ?", (order["id"],)
    ).one()
    if payment is None:
        idempotency.release(idempotency_key)
        raise HTTPException(status_code=409, detail="Order has no payment to refund")

    try:
        amount = Decimal(payment["amount"])
        logger.info("refunding payment %s", redact({"order_id": order["id"], "amount": str(amount)}))
        result = payment_gateway.refund(payment["charge_id"], amount)
        with db.transaction():
            db.execute(
                "INSERT INTO refunds (order_id, refund_id, amount) VALUES (?, ?, ?)",
                (order["id"], result["id"], str(amount)),
            )
            order_state.transition(order, "refunded")
        response = {"order_id": order["id"], "refund_id": result["id"], "amount": str(amount), "status": "refunded"}
        idempotency.save_response(idempotency_key, response)
    except Exception:
        idempotency.release(idempotency_key)
        logger.exception("refund failed for order %s", order["id"])
        raise HTTPException(status_code=502, detail="Refund could not be processed")

    try:
        email_client.send(user.email, "Your refund", f"We refunded ${amount} for order {order['id']}.")
    except email_client.EmailError:
        logger.exception("refund receipt email failed for order %s", order["id"])
    return response
