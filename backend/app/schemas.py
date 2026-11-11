"""Request/response helpers for the HTTP API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from . import constants as C
from .models import Incident, Investigation, Order, TransactionEvent


class OpenIncidentIn(BaseModel):
    transaction_id: str = Field(min_length=3, max_length=80, description="PayPal order ID, capture ID, or incident reference")


class EventIn(BaseModel):
    source: str = C.SOURCE_LOG
    event_type: str
    timestamp: datetime | None = None
    external_event_id: str | None = Field(default=None, max_length=120)
    http_status: int | None = None
    error_code: str | None = Field(default=None, max_length=80)
    transaction_state: str | None = Field(default=None, max_length=24)
    correlation_id: str | None = Field(default=None, max_length=120)
    capture_ref: str | None = Field(default=None, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    is_synthetic: bool = False


class InvestigateIn(BaseModel):
    use_llm: bool = True
    verify: bool = Field(default=True, description="Let the investigator re-check the current state with PayPal (read-only) first")
    actor: str = Field(default="user", max_length=80)


class ResolveIn(BaseModel):
    resolved_by: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=1000)


class CreateOrderIn(BaseModel):
    amount: str = Field(pattern=r"^\d{1,9}(\.\d{1,2})?$", description="e.g. '25.00'")
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    description: str | None = Field(default=None, max_length=127)


class CaptureIn(BaseModel):
    approved_by: str = Field(min_length=1, max_length=80, description="Human who explicitly approves this payment operation")
    confirm: bool = Field(description="Must be true: explicit human approval")
    simulate_timeout: bool = Field(default=False, description="DEMO ONLY: cut this capture's wait for PayPal's response short to produce a genuine client-side timeout")


class LogIn(BaseModel):
    log_text: str = Field(min_length=10, max_length=20000, description="Raw merchant application log (pasted)")
    use_llm: bool = True


def incident_dict(inc: Incident) -> dict:
    return {
        "id": inc.id, "reference": inc.reference, "incident_type": inc.incident_type, "severity": inc.severity,
        "severity_reasons": inc.severity_reasons, "status": inc.status, "order_id": inc.order_id, "capture_id": inc.capture_id,
        "title": inc.title, "amount": inc.amount, "currency": inc.currency, "is_synthetic": inc.is_synthetic,
        "data_origin": "synthetic" if inc.is_synthetic else "sandbox", "scenario": inc.scenario,
        "outcome_certainty": inc.outcome_certainty, "authoritative_state": inc.authoritative_state, "local_state": inc.local_state,
        "created_at": inc.created_at, "updated_at": inc.updated_at, "resolved_at": inc.resolved_at,
        "resolved_by": inc.resolved_by, "resolution_note": inc.resolution_note,
    }


def investigation_dict(inv: Investigation | None) -> dict | None:
    if inv is None:
        return None
    return {
        "id": inv.id, "incident_id": inv.incident_id, "summary": inv.summary, "root_cause": inv.root_cause,
        "supporting_evidence": inv.supporting_evidence, "contradictory_evidence": inv.contradictory_evidence,
        "alternative_explanations": inv.alternative_explanations, "confidence": inv.confidence,
        "evidence_confidence": inv.evidence_confidence, "llm_confidence": inv.llm_confidence,
        "recommended_action": inv.recommended_action, "prohibited_actions": inv.prohibited_actions,
        "missing_information": inv.missing_information, "generated_by": inv.generated_by,
        "validation": inv.validation, "created_at": inv.created_at,
    }


def event_dict(e: TransactionEvent) -> dict:
    return {
        "id": e.id, "incident_id": e.incident_id, "order_ref": e.order_ref, "capture_ref": e.capture_ref, "source": e.source,
        "event_type": e.event_type, "external_event_id": e.external_event_id, "timestamp": e.timestamp, "http_status": e.http_status,
        "error_code": e.error_code, "transaction_state": e.transaction_state, "correlation_id": e.correlation_id, "verified": e.verified,
        "is_synthetic": e.is_synthetic, "delivery_count": e.delivery_count, "payload_redacted": e.payload_redacted,
    }


def order_dict(o: Order) -> dict:
    return {
        "order_ref": o.order_ref, "capture_id": o.capture_id, "amount": o.amount, "currency": o.currency, "status": o.status,
        "local_state": o.local_state, "is_synthetic": o.is_synthetic, "data_origin": "synthetic" if o.is_synthetic else "sandbox",
        "payment_processing_count": o.payment_processing_count, "created_at": o.created_at,
    }
