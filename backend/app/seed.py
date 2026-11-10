"""Synthetic incident fixtures (PRD section 6). Every row is flagged is_synthetic=True.

Events go through the REAL ingestion path (service.record_event), so deduplication and
incident detection are genuinely exercised - not faked.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import constants as C
from . import service
from .models import AuditLog, Incident, Investigation, Order, TransactionEvent
from .timeutil import utcnow

SCENARIOS = {
    "timeout_after_success": {"alias": "A", "title": "Timeout after successful capture"},
    "payment_pending": {"alias": "B", "title": "Payment remains pending"},
    "duplicate_webhook": {"alias": "C", "title": "Duplicate event delivery"},
    "state_mismatch": {"alias": "D", "title": "Merchant records disagree with PayPal"},
}
ALIASES = {v["alias"]: k for k, v in SCENARIOS.items()}


def resolve_scenario(name: str) -> str | None:
    if name in SCENARIOS:
        return name
    return ALIASES.get(name.upper())


def _ids(prefix: str) -> tuple[str, str, str]:
    h = uuid4().hex[:10].upper()
    return f"SYN-ORD-{h}", f"SYN-CAP-{h}", h


class _Seeder:
    def __init__(self, db: Session, order_ref: str, t0: datetime, amount: float, scenario: str):
        self.db, self.order_ref, self.t0, self.amount, self.scenario = db, order_ref, t0, amount, scenario

    def ev(self, offset_s: float, **kw):
        return service.record_event(
            self.db, order_ref=self.order_ref, timestamp=self.t0 + timedelta(seconds=offset_s), is_synthetic=True,
            amount=self.amount, currency="USD", scenario=self.scenario, refresh=False, actor="demo-seed", **kw)


def _common_start(s: _Seeder) -> None:
    s.ev(0, source=C.SOURCE_API, event_type=C.ET_ORDER_CREATED, transaction_state="CREATED", http_status=201, verified=True,
         correlation_id="syn-debug-" + uuid4().hex[:8], payload={"amount": {"value": f"{s.amount:.2f}", "currency_code": "USD"}})
    s.ev(25, source=C.SOURCE_WEBHOOK, event_type=C.ET_ORDER_APPROVED, transaction_state="APPROVED", verified=True,
         external_event_id="WH-SYN-" + uuid4().hex[:12].upper(), payload={"paypal_event_type": "CHECKOUT.ORDER.APPROVED"})
    s.ev(60, source=C.SOURCE_LOG, event_type=C.ET_CAPTURE_REQUESTED, correlation_id="req-" + uuid4().hex[:8], payload={"note": "POST /v2/checkout/orders/{id}/capture"})


def seed_timeout_after_success(db: Session) -> str:
    ref, cap, _ = _ids("A")
    s = _Seeder(db, ref, utcnow() - timedelta(hours=4), 320.00, "timeout_after_success")
    _common_start(s)
    s.ev(90, source=C.SOURCE_LOG, event_type=C.ET_REQUEST_TIMEOUT, payload={"timeout_ms": 30000, "message": "Request to PayPal timed out"})
    s.ev(105, source=C.SOURCE_WEBHOOK, event_type=C.ET_CAPTURE_COMPLETED, transaction_state="COMPLETED", verified=True, capture_ref=cap,
         external_event_id="WH-SYN-" + uuid4().hex[:12].upper(), payload={"paypal_event_type": "PAYMENT.CAPTURE.COMPLETED", "amount": {"value": "320.00", "currency_code": "USD"}})
    s.ev(110, source=C.SOURCE_LOG, event_type=C.ET_LOCAL_STATE, transaction_state="PENDING", payload={"note": "Order left in PENDING after timeout"})
    return ref


def seed_payment_pending(db: Session) -> str:
    ref, cap, _ = _ids("B")
    s = _Seeder(db, ref, utcnow() - timedelta(hours=7), 89.50, "payment_pending")
    _common_start(s)
    s.ev(75, source=C.SOURCE_WEBHOOK, event_type=C.ET_CAPTURE_PENDING, transaction_state="PENDING", verified=True, capture_ref=cap,
         external_event_id="WH-SYN-" + uuid4().hex[:12].upper(), payload={"paypal_event_type": "PAYMENT.CAPTURE.PENDING", "amount": {"value": "89.50", "currency_code": "USD"}})
    s.ev(80, source=C.SOURCE_LOG, event_type=C.ET_LOCAL_STATE, transaction_state="PENDING", payload={"note": "Order marked PENDING; waiting for final event"})
    return ref


def seed_duplicate_webhook(db: Session) -> str:
    ref, cap, h = _ids("C")
    s = _Seeder(db, ref, utcnow() - timedelta(minutes=30), 35.00, "duplicate_webhook")
    _common_start(s)
    s.ev(80, source=C.SOURCE_API, event_type=C.ET_CAPTURE_COMPLETED, transaction_state="COMPLETED", http_status=201, verified=True, capture_ref=cap,
         correlation_id="syn-debug-" + uuid4().hex[:8], payload={"amount": {"value": "35.00", "currency_code": "USD"}})
    wh_id = "WH-SYN-DUP-" + h
    for i, off in enumerate((95, 215, 455)):  # the SAME event ID delivered three times
        s.ev(off, source=C.SOURCE_WEBHOOK, event_type=C.ET_CAPTURE_COMPLETED, transaction_state="COMPLETED", verified=True, capture_ref=cap,
             external_event_id=wh_id, payload={"paypal_event_type": "PAYMENT.CAPTURE.COMPLETED", "amount": {"value": "35.00", "currency_code": "USD"}})
    s.ev(100, source=C.SOURCE_LOG, event_type=C.ET_LOCAL_STATE, transaction_state="COMPLETED", payload={"note": "Order marked COMPLETED"})
    return ref


def seed_state_mismatch(db: Session) -> str:
    ref, cap, _ = _ids("D")
    s = _Seeder(db, ref, utcnow() - timedelta(hours=2), 1250.00, "state_mismatch")
    _common_start(s)
    s.ev(85, source=C.SOURCE_API, event_type=C.ET_CAPTURE_COMPLETED, transaction_state="COMPLETED", http_status=201, verified=True, capture_ref=cap,
         correlation_id="syn-debug-" + uuid4().hex[:8], payload={"amount": {"value": "1250.00", "currency_code": "USD"}})
    s.ev(92, source=C.SOURCE_LOG, event_type=C.ET_LOCAL_STATE, transaction_state="FAILED", payload={"message": "Merchant app marked the order FAILED after a response-handling error"})
    return ref


SEEDERS = {
    "timeout_after_success": seed_timeout_after_success,
    "payment_pending": seed_payment_pending,
    "duplicate_webhook": seed_duplicate_webhook,
    "state_mismatch": seed_state_mismatch,
}


def run_scenario(db: Session, scenario: str):
    """Create one synthetic incident and return the Incident (auto-detected from the events)."""
    order_ref = SEEDERS[scenario](db)
    incident = service.refresh_order(db, order_ref)
    if incident is None:  # defensive: scenario should always yield an incident
        incident = service.open_incident(db, order_ref, actor="demo-seed")
    service.audit(db, actor="demo", action="scenario_triggered", incident_id=incident.id, order_ref=order_ref,
                  details={"scenario": scenario, "synthetic": True})
    return incident


def reset_synthetic(db: Session) -> int:
    """Delete ONLY synthetic data."""
    inc_ids = [i for i in db.scalars(select(Incident.id).where(Incident.is_synthetic.is_(True)))]
    refs = [r for r in db.scalars(select(Order.order_ref).where(Order.is_synthetic.is_(True)))]
    if inc_ids:
        db.execute(delete(Investigation).where(Investigation.incident_id.in_(inc_ids)))
        db.execute(delete(AuditLog).where(AuditLog.incident_id.in_(inc_ids)))
    if refs:
        db.execute(delete(TransactionEvent).where(TransactionEvent.order_ref.in_(refs)))
        db.execute(delete(AuditLog).where(AuditLog.order_ref.in_(refs)))
    if inc_ids:
        db.execute(delete(Incident).where(Incident.id.in_(inc_ids)))
    if refs:
        db.execute(delete(Order).where(Order.order_ref.in_(refs)))
    db.commit()
    return len(inc_ids)
