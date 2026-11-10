"""PayPal webhook processing: verify signature -> normalize -> record idempotently."""
from __future__ import annotations

import json

from . import paypal, service
from .config import get_settings
from .db import SessionLocal
from .events import normalize_webhook


def process_webhook(raw: bytes, headers: dict) -> tuple[int, dict]:
    """Returns (http_status, body). 2xx tells PayPal not to retry; 5xx asks PayPal to retry later."""
    settings = get_settings()
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return 400, {"status": "rejected", "reason": "invalid JSON"}

    verified = False
    with SessionLocal() as db:
        if settings.paypal_webhook_id:
            try:
                verified = paypal.verify_webhook_signature(headers, raw, settings.paypal_webhook_id)
            except (paypal.PayPalError, paypal.PayPalTimeout, paypal.PayPalNotConfigured) as e:
                service.audit(db, actor="webhook", action="webhook_verification_unavailable", details={"error": str(e)})
                return 503, {"status": "retry_later", "reason": "signature verification unavailable"}  # fail safe: do not trust
            if not verified:
                service.audit(db, actor="webhook", action="webhook_rejected_bad_signature",
                              details={"event_id": body.get("id"), "event_type": body.get("event_type")})
                return 400, {"status": "rejected", "reason": "signature verification failed"}
        elif not settings.allow_unverified_webhooks:
            return 503, {"status": "retry_later", "reason": "PAYPAL_WEBHOOK_ID is not configured; refusing to trust unverified events"}

        norm = normalize_webhook(body)
        result = service.record_event(db, verified=verified, is_synthetic=False, actor="webhook", **norm)
        status = "processed" if result.created else "duplicate_ignored"
        if result.created and not verified:
            status = "stored_unverified"
        return 200, {"status": status, "event_id": body.get("id"), "delivery_count": result.event.delivery_count,
                     "incident": result.incident.reference if result.incident else None}
