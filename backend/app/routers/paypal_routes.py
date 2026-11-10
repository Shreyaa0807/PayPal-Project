from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from .. import paypal_ops
from ..db import get_db
from ..schemas import CaptureIn, CreateOrderIn
from ..security import require_api_key
from fastapi import HTTPException

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/paypal/orders", status_code=201)
def create_order(body: CreateOrderIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(get_db)):
    """Create a PayPal Sandbox order. Redirect the buyer to `approve_url`, then call the capture endpoint."""
    return paypal_ops.create_order(db, body.amount, body.currency, body.description, idempotency_key)


@router.get("/paypal/orders/{order_id}")
def get_order(order_id: str, db: Session = Depends(get_db)):
    """Retrieve the order's CURRENT state from PayPal (through the backend) and record it as evidence."""
    body, result = paypal_ops.lookup_order(db, order_id)
    state = result.event.transaction_state
    return {"order_id": order_id, "state": state, "paypal_status": body.get("status"), "capture_id": result.event.capture_ref,
            "incident": result.incident.reference if result.incident else None, "data_origin": "sandbox"}


@router.post("/paypal/orders/{order_id}/capture")
def capture_order(order_id: str, body: CaptureIn, db: Session = Depends(get_db)):
    """Human-approved capture. The safety engine blocks it if a capture is already confirmed or its outcome is unverified."""
    if not body.confirm:
        raise HTTPException(400, "Explicit human approval required: set confirm=true.")
    return paypal_ops.capture(db, order_id, body.approved_by)
