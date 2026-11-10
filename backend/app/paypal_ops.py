"""PayPal operations that also record evidence (every call becomes a normalized event)."""
from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import constants as C
from . import paypal, service
from .events import normalize_order_lookup
from .safety import can_capture


def _http(e: Exception) -> HTTPException:
    if isinstance(e, paypal.PayPalNotConfigured):
        return HTTPException(503, f"PayPal is not configured: {e}")
    if isinstance(e, paypal.PayPalTimeout):
        return HTTPException(504, "PayPal did not respond in time. The outcome is unknown; check the current state before retrying.")
    if isinstance(e, paypal.PayPalError):
        return HTTPException(502 if e.status >= 500 else 422, {"paypal_status": e.status, "error": e.error_code, "debug_id": e.debug_id})
    return HTTPException(500, "Unexpected PayPal error")


def create_order(db: Session, amount: str, currency: str, description: str | None, request_id: str | None) -> dict:
    try:
        resp = paypal.create_order(amount, currency, description, request_id)
    except (paypal.PayPalError, paypal.PayPalTimeout, paypal.PayPalNotConfigured) as e:
        service.audit(db, actor="system", action="paypal_create_order_failed", details={"error": str(e)})
        raise _http(e)
    order_id = resp.body["id"]
    service.record_event(db, order_ref=order_id, source=C.SOURCE_API, event_type=C.ET_ORDER_CREATED, transaction_state="CREATED",
                         http_status=resp.status, correlation_id=resp.debug_id, verified=True, amount=float(amount), currency=currency,
                         payload={"amount": {"value": amount, "currency_code": currency}, "status": resp.body.get("status")})
    order = service.get_order(db, order_id)
    order.capture_request_id = str(uuid4())  # reused on any capture retry => PayPal idempotency
    db.commit()
    return {"order_id": order_id, "status": resp.body.get("status"), "approve_url": paypal.approve_link(resp.body),
            "data_origin": "sandbox", "debug_id": resp.debug_id}


def lookup_order(db: Session, order_id: str, actor: str = "user") -> tuple[dict, object]:
    """Fetch the authoritative order state from PayPal and record it as evidence."""
    try:
        resp = paypal.get_order(order_id)
    except paypal.PayPalError as e:
        service.record_event(db, order_ref=order_id, source=C.SOURCE_API, event_type=C.ET_API_ERROR, http_status=e.status,
                             error_code=e.error_code, correlation_id=e.debug_id, payload={"message": e.body.get("message")}, actor=actor)
        raise _http(e)
    except (paypal.PayPalTimeout, paypal.PayPalNotConfigured) as e:
        raise _http(e)  # a failed *lookup* is not recorded as a payment timeout
    result = service.record_event(db, actor=actor, **normalize_order_lookup(order_id, resp.body, resp.status, resp.debug_id))
    return resp.body, result


def capture(db: Session, order_id: str, approved_by: str) -> dict:
    order = service.get_order(db, order_id)
    if order is None:
        raise HTTPException(404, "Unknown order. Create it through this API or look it up first.")
    a = service.analyze_order(db, order_id)
    ok, reason = can_capture(a)
    if not ok:
        service.audit(db, actor=approved_by, action="capture_blocked_by_safety_engine", order_ref=order_id, details={"reason": reason})
        raise HTTPException(409, {"blocked": True, "reason": reason, "hint": "GET /api/paypal/orders/{id} to retrieve the authoritative state."})

    request_id = order.capture_request_id or str(uuid4())
    order.capture_request_id = request_id
    db.commit()
    service.audit(db, actor=approved_by, action="capture_approved_by_human", order_ref=order_id, details={"request_id": request_id})
    service.record_event(db, order_ref=order_id, source=C.SOURCE_LOG, event_type=C.ET_CAPTURE_REQUESTED, correlation_id=request_id,
                         payload={"note": "Capture submitted with PayPal-Request-Id"}, refresh=False, actor=approved_by)
    try:
        resp = paypal.capture_order(order_id, request_id)
    except paypal.PayPalTimeout:
        service.record_event(db, order_ref=order_id, source=C.SOURCE_LOG, event_type=C.ET_REQUEST_TIMEOUT, correlation_id=request_id,
                             payload={"message": "Capture request to PayPal timed out"}, refresh=False)
        service.record_event(db, order_ref=order_id, source=C.SOURCE_LOG, event_type=C.ET_LOCAL_STATE, transaction_state="PENDING",
                             payload={"note": "Order left PENDING after timeout"})
        return {"outcome": "timeout_unknown", "message": "Capture timed out. The payment may still have succeeded. Do NOT capture again; check the current state.",
                "incident": _inc_ref(db, order_id)}
    except paypal.PayPalError as e:
        service.record_event(db, order_ref=order_id, source=C.SOURCE_API, event_type=C.ET_API_ERROR, http_status=e.status, error_code=e.error_code,
                             correlation_id=e.debug_id, payload={"message": e.body.get("message")})
        return {"outcome": "api_error", "paypal_status": e.status, "error": e.error_code, "debug_id": e.debug_id, "incident": _inc_ref(db, order_id)}

    cap = (((resp.body.get("purchase_units") or [{}])[0].get("payments") or {}).get("captures") or [{}])[0]
    state = (cap.get("status") or resp.body.get("status") or "").upper() or None
    etype = {"COMPLETED": C.ET_CAPTURE_COMPLETED, "PENDING": C.ET_CAPTURE_PENDING, "DECLINED": C.ET_CAPTURE_DENIED}.get(state or "", C.ET_OTHER)
    service.record_event(db, order_ref=order_id, source=C.SOURCE_API, event_type=etype, transaction_state=state, http_status=resp.status,
                         correlation_id=resp.debug_id, capture_ref=cap.get("id"), verified=True, payload={"status": state, "amount": cap.get("amount")},
                         refresh=False)
    local = {"COMPLETED": "COMPLETED", "PENDING": "PENDING", "DECLINED": "FAILED"}.get(state or "", "PENDING")
    service.record_event(db, order_ref=order_id, source=C.SOURCE_LOG, event_type=C.ET_LOCAL_STATE, transaction_state=local,
                         payload={"note": "Merchant app updated local order from capture response"})
    return {"outcome": (state or "unknown").lower(), "capture_id": cap.get("id"), "debug_id": resp.debug_id, "incident": _inc_ref(db, order_id)}


def _inc_ref(db: Session, order_id: str) -> str | None:
    inc = service.latest_incident(db, order_id)
    return inc.reference if inc else None
