"""Normalize PayPal API responses / webhooks into the common internal event schema (PRD FR-2)."""
from __future__ import annotations

from datetime import datetime

from . import constants as C
from .redact import redact
from .timeutil import parse_ts

WEBHOOK_MAP: dict[str, tuple[str, str | None]] = {
    "PAYMENT.CAPTURE.COMPLETED": (C.ET_CAPTURE_COMPLETED, "COMPLETED"),
    "PAYMENT.CAPTURE.PENDING": (C.ET_CAPTURE_PENDING, "PENDING"),
    "PAYMENT.CAPTURE.DENIED": (C.ET_CAPTURE_DENIED, "DECLINED"),
    "PAYMENT.CAPTURE.DECLINED": (C.ET_CAPTURE_DENIED, "DECLINED"),
    "PAYMENT.CAPTURE.REFUNDED": (C.ET_CAPTURE_REFUNDED, "REFUNDED"),
    "CHECKOUT.ORDER.APPROVED": (C.ET_ORDER_APPROVED, "APPROVED"),
    "CHECKOUT.ORDER.COMPLETED": (C.ET_ORDER_COMPLETED, "COMPLETED"),
}


def _first_amount(resource: dict) -> dict | None:
    if isinstance(resource.get("amount"), dict):
        return resource["amount"]
    units = resource.get("purchase_units") or []
    if units and isinstance(units[0].get("amount"), dict):
        return units[0]["amount"]
    return None


def normalize_webhook(body: dict) -> dict:
    """Return kwargs for service.record_event (minus `verified`)."""
    et = body.get("event_type", "") or ""
    res = body.get("resource") or {}
    event_type, state = WEBHOOK_MAP.get(et, (C.ET_OTHER, None))
    order_ref = capture_ref = None
    if et.startswith("PAYMENT.CAPTURE."):
        capture_ref = res.get("id")
        order_ref = ((res.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
    elif et.startswith("CHECKOUT.ORDER."):
        order_ref = res.get("id")
        for pu in res.get("purchase_units") or []:
            caps = ((pu.get("payments") or {}).get("captures")) or []
            if caps:
                capture_ref = caps[0].get("id")
    order_ref = order_ref or (f"UNLINKED:{capture_ref or body.get('id')}")
    amount = _first_amount(res) or {}
    return {
        "order_ref": order_ref,
        "capture_ref": capture_ref,
        "source": C.SOURCE_WEBHOOK,
        "event_type": event_type,
        "transaction_state": state,
        "external_event_id": body.get("id"),
        "timestamp": parse_ts(body.get("create_time")),
        "amount": float(amount["value"]) if amount.get("value") else None,
        "currency": amount.get("currency_code"),
        "payload": {
            "paypal_event_type": et,
            "summary": body.get("summary"),
            "resource_status": res.get("status"),
            "status_details": res.get("status_details"),
            "amount": amount or None,
        },
    }


def normalize_order_lookup(order_id: str, body: dict, http_status: int, debug_id: str | None) -> dict:
    """GET /v2/checkout/orders/{id} response -> STATE_LOOKUP event."""
    state = (body.get("status") or "").upper() or None
    capture_ref = None
    amount = None
    for pu in body.get("purchase_units") or []:
        amount = amount or pu.get("amount")
        caps = ((pu.get("payments") or {}).get("captures")) or []
        if caps:
            capture_ref = caps[0].get("id")
            state = (caps[0].get("status") or state or "").upper() or None
    return {
        "order_ref": order_id, "capture_ref": capture_ref, "source": C.SOURCE_API, "event_type": C.ET_STATE_LOOKUP,
        "transaction_state": state, "http_status": http_status, "correlation_id": debug_id, "verified": True,
        "payload": redact({"order_status": body.get("status"), "amount": amount}),
        "amount": float(amount["value"]) if amount and amount.get("value") else None,
        "currency": (amount or {}).get("currency_code"),
    }
