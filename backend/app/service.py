"""Core orchestration: record events (idempotently), refresh incidents, audit logging, investigations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import constants as C
from .config import get_settings
from .models import AuditLog, Incident, Investigation, Order, TransactionEvent
from .redact import redact
from .safety import SafetyResult, evaluate
from .severity import compute_severity
from .timeline import TimelineAnalysis, build_analysis, is_authoritative
from .timeutil import as_utc, utcnow

TITLES = {
    "timeout_after_success": "Capture timed out but PayPal shows it completed",
    "payment_pending": "Payment remains pending with no final event",
    "duplicate_event_delivery": "Same payment webhook delivered more than once",
    "state_mismatch": "Merchant record disagrees with PayPal's transaction state",
    "outcome_unknown": "Payment outcome unknown after missing response",
    "conflicting_evidence": "Conflicting payment evidence",
    "potential_duplicate_charge": "Multiple captures completed for one order",
    "api_error": "PayPal API error during payment",
    "unclassified": "Transaction under review",
}


@dataclass
class RecordResult:
    event: TransactionEvent
    created: bool
    incident: Incident | None


def audit(db: Session, *, actor: str, action: str, details: dict | None = None,
          incident_id: str | None = None, order_ref: str | None = None, commit: bool = True) -> AuditLog:
    row = AuditLog(incident_id=incident_id, order_ref=order_ref, actor=actor, action=action, details_redacted=redact(details or {}))
    db.add(row)
    if commit:
        db.commit()
    return row


def get_order(db: Session, order_ref: str) -> Order | None:
    return db.scalar(select(Order).where(Order.order_ref == order_ref))


def get_or_create_order(db: Session, order_ref: str, **defaults) -> Order:
    order = get_order(db, order_ref)
    if order is None:
        order = Order(order_ref=order_ref, processed_capture_ids=[], **{k: v for k, v in defaults.items() if v is not None})
        db.add(order)
        db.flush()
    return order


def latest_incident(db: Session, order_ref: str) -> Incident | None:
    return db.scalar(select(Incident).where(Incident.order_id == order_ref).order_by(Incident.created_at.desc()).limit(1))


def record_event(
    db: Session, *, order_ref: str, source: str, event_type: str, timestamp: datetime | None = None,
    external_event_id: str | None = None, http_status: int | None = None, error_code: str | None = None,
    transaction_state: str | None = None, correlation_id: str | None = None, capture_ref: str | None = None,
    verified: bool = False, is_synthetic: bool = False, payload: dict | None = None,
    amount: float | None = None, currency: str | None = None, scenario: str | None = None,
    refresh: bool = True, actor: str = "system",
) -> RecordResult:
    """Insert one normalized event. A repeated provider event ID is detected and processed idempotently."""
    now = utcnow()
    order = get_or_create_order(db, order_ref, amount=amount, currency=currency, is_synthetic=is_synthetic or None, scenario=scenario)
    if order.amount is None and amount is not None:
        order.amount = amount

    if external_event_id:
        existing = db.scalar(select(TransactionEvent).where(TransactionEvent.external_event_id == external_event_id))
        if existing is not None:
            return _mark_duplicate(db, existing, now, refresh)

    event = TransactionEvent(
        order_ref=order_ref, capture_ref=capture_ref, source=source, event_type=event_type, external_event_id=external_event_id,
        timestamp=as_utc(timestamp) if timestamp else now, http_status=http_status, error_code=error_code,
        transaction_state=(transaction_state or None) and transaction_state.upper(), correlation_id=correlation_id,
        verified=verified, is_synthetic=is_synthetic, payload_redacted=redact(payload or {}), last_delivery_at=now,
    )
    inc = latest_incident(db, order_ref)
    if inc:
        event.incident_id = inc.id
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError:  # lost a race with a concurrent delivery of the same event ID
        db.expire_all()
        existing = db.scalar(select(TransactionEvent).where(TransactionEvent.external_event_id == external_event_id))
        if existing is None:
            raise
        return _mark_duplicate(db, existing, now, refresh)

    # Side effects run ONCE per distinct event: update the merchant-side order record.
    if event_type == C.ET_LOCAL_STATE:
        order.local_state = event.transaction_state
    if is_authoritative(event) and C.state_class(event.transaction_state) == "success" and capture_ref:
        done = list(order.processed_capture_ids or [])
        if capture_ref not in done:  # idempotent by capture ID
            done.append(capture_ref)
            order.processed_capture_ids = done
            order.payment_processing_count = len(done)
        order.capture_id = order.capture_id or capture_ref
    elif capture_ref and not order.capture_id:
        order.capture_id = capture_ref
    db.commit()
    incident = refresh_order(db, order_ref) if refresh else inc
    return RecordResult(event, True, incident)


def _mark_duplicate(db: Session, existing: TransactionEvent, now: datetime, refresh: bool) -> RecordResult:
    existing.delivery_count = (existing.delivery_count or 1) + 1
    existing.last_delivery_at = now
    audit(db, actor="webhook", action="duplicate_event_ignored", incident_id=existing.incident_id, order_ref=existing.order_ref, commit=False,
          details={"external_event_id": existing.external_event_id, "delivery_count": existing.delivery_count, "note": "Processed once; repeat delivery ignored."})
    db.commit()
    incident = refresh_order(db, existing.order_ref) if refresh else latest_incident(db, existing.order_ref)
    return RecordResult(existing, False, incident)


def order_events(db: Session, order_ref: str) -> list[TransactionEvent]:
    return list(db.scalars(select(TransactionEvent).where(TransactionEvent.order_ref == order_ref).order_by(TransactionEvent.timestamp)))


def analyze_order(db: Session, order_ref: str, now: datetime | None = None) -> TimelineAnalysis:
    return build_analysis(order_events(db, order_ref), get_order(db, order_ref), now)


def _status_from(a: TimelineAnalysis) -> str:
    cls = C.state_class(a.authoritative_state)
    if a.authoritative_state == "CONFLICT":
        return "pending"
    return {"success": "completed", "failure": "failed", "refunded": "completed"}.get(cls or "", "pending")


def refresh_order(db: Session, order_ref: str, now: datetime | None = None) -> Incident | None:
    """Re-analyse an order. Creates an incident if an anomaly is detected; updates an open one."""
    order = get_order(db, order_ref)
    if order is None:
        return None
    a = analyze_order(db, order_ref, now)
    order.status = _status_from(a)
    order.local_state = a.local_state or order.local_state
    inc = latest_incident(db, order_ref)
    if a.incident_type is None and inc is None:
        db.commit()
        return None
    if inc is not None and inc.status == "resolved":
        db.commit()
        return inc

    if inc is None:
        inc = Incident(reference="INC-" + uuid4().hex[:6].upper(), incident_type=a.incident_type, severity="Low", order_id=order_ref,
                       status="open", is_synthetic=a.is_synthetic, scenario=order.scenario, currency=order.currency)
        db.add(inc)
        db.flush()
    if a.incident_type:
        inc.incident_type = a.incident_type
    others = db.scalar(select(func.count()).select_from(Incident).where(
        Incident.incident_type == inc.incident_type, Incident.status != "resolved", Incident.id != inc.id)) or 0
    sev = compute_severity(a, affected_others=others, now=now)
    inc.severity, inc.severity_reasons = sev["level"], sev
    inc.title = TITLES.get(inc.incident_type, TITLES["unclassified"])
    inc.amount, inc.currency, inc.capture_id = order.amount, order.currency, order.capture_id
    inc.outcome_certainty, inc.authoritative_state, inc.local_state = a.outcome_certainty, a.authoritative_state, a.local_state
    inc.is_synthetic = a.is_synthetic
    db.execute(update(TransactionEvent).where(TransactionEvent.order_ref == order_ref, TransactionEvent.incident_id.is_(None)).values(incident_id=inc.id))
    db.commit()
    return inc


def open_incident(db: Session, order_ref: str, actor: str = "user") -> Incident:
    """User submits a transaction ID: reuse the incident, or open one even if no anomaly was auto-detected."""
    inc = latest_incident(db, order_ref)
    if inc:
        return inc
    order = get_order(db, order_ref)
    a = analyze_order(db, order_ref)
    inc = Incident(reference="INC-" + uuid4().hex[:6].upper(), incident_type=a.incident_type or "unclassified", severity="Low",
                   order_id=order_ref, status="open", is_synthetic=a.is_synthetic, scenario=order.scenario if order else None,
                   currency=order.currency if order else "USD")
    db.add(inc)
    db.flush()
    sev = compute_severity(a)
    inc.severity, inc.severity_reasons = sev["level"], sev
    inc.title = TITLES.get(inc.incident_type, TITLES["unclassified"])
    inc.amount, inc.capture_id = (order.amount if order else None), (order.capture_id if order else None)
    inc.outcome_certainty, inc.authoritative_state, inc.local_state = a.outcome_certainty, a.authoritative_state, a.local_state
    db.execute(update(TransactionEvent).where(TransactionEvent.order_ref == order_ref, TransactionEvent.incident_id.is_(None)).values(incident_id=inc.id))
    audit(db, actor=actor, action="incident_opened", incident_id=inc.id, order_ref=order_ref, details={"reference": inc.reference}, commit=False)
    db.commit()
    return inc


def run_investigation(db: Session, incident: Incident, actor: str = "user", use_llm: bool = True) -> tuple[Investigation, TimelineAnalysis, SafetyResult]:
    from .ai.investigator import investigate  # local import keeps the AI layer optional at import time

    inc = refresh_order(db, incident.order_id) or incident
    if inc.status == "open":
        inc.status = "investigating"
    a = analyze_order(db, inc.order_id)
    safety = evaluate(a)
    sev = compute_severity(a)
    result = investigate(a, safety, sev, {"reference": inc.reference, "incident_type": inc.incident_type, "severity": inc.severity,
                                           "is_synthetic": inc.is_synthetic}, use_llm=use_llm)
    d = result.diagnosis
    inv = Investigation(
        incident_id=inc.id, summary=d.summary, root_cause=d.root_cause,
        supporting_evidence=[e.model_dump() for e in d.supporting_evidence],
        contradictory_evidence=[e.model_dump() for e in d.contradictory_evidence],
        alternative_explanations=d.alternative_explanations, confidence=d.confidence,
        evidence_confidence=result.evidence_confidence, llm_confidence=result.llm_confidence,
        recommended_action=d.recommended_action.model_dump(), prohibited_actions=[p.model_dump() for p in d.prohibited_actions],
        missing_information=d.missing_information, generated_by=result.generated_by,
        validation=result.validation, safety=safety.model_dump(),
    )
    db.add(inv)
    audit(db, actor=actor, action="investigation_run", incident_id=inc.id, order_ref=inc.order_id, commit=False,
          details={"generated_by": result.generated_by, "confidence": d.confidence, "validation": result.validation})
    db.commit()
    return inv, a, safety


def latest_investigation(db: Session, incident_id: str) -> Investigation | None:
    return db.scalar(select(Investigation).where(Investigation.incident_id == incident_id).order_by(Investigation.created_at.desc()).limit(1))
