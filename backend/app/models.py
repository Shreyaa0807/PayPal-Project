"""Database tables (PRD section 9, plus an `orders` table for local order state)."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .timeutil import utcnow

JsonType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid4())


class Order(Base):
    """The merchant's view of an order (real sandbox order or synthetic fixture)."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    order_ref: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    capture_id: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[str] = mapped_column(String(24), default="pending")  # derived from PayPal evidence
    local_state: Mapped[str | None] = mapped_column(String(24), nullable=True)  # what the merchant app believes
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    scenario: Mapped[str | None] = mapped_column(String(40), nullable=True)
    capture_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # PayPal-Request-Id reused on retries
    processed_capture_ids: Mapped[list] = mapped_column(JsonType, default=list)
    payment_processing_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    incident_type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(12), index=True)
    severity_reasons: Mapped[dict] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | investigating | resolved
    order_id: Mapped[str] = mapped_column(String(80), index=True)
    capture_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    scenario: Mapped[str | None] = mapped_column(String(40), nullable=True)
    outcome_certainty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    authoritative_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    local_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TransactionEvent(Base):
    __tablename__ = "transaction_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    order_ref: Mapped[str] = mapped_column(String(80), index=True)
    capture_ref: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(24))  # paypal_api | paypal_webhook | merchant_log
    event_type: Mapped[str] = mapped_column(String(32))
    # Provider event IDs are unique -> a repeated webhook delivery can never create a second row.
    external_event_id: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    transaction_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_count: Mapped[int] = mapped_column(Integer, default=1)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_redacted: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    supporting_evidence: Mapped[list] = mapped_column(JsonType, default=list)
    contradictory_evidence: Mapped[list] = mapped_column(JsonType, default=list)
    alternative_explanations: Mapped[list] = mapped_column(JsonType, default=list)
    confidence: Mapped[str] = mapped_column(String(16))
    evidence_confidence: Mapped[str] = mapped_column(String(16))
    llm_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    recommended_action: Mapped[dict] = mapped_column(JsonType, default=dict)
    prohibited_actions: Mapped[list] = mapped_column(JsonType, default=list)
    missing_information: Mapped[list] = mapped_column(JsonType, default=list)
    generated_by: Mapped[str] = mapped_column(String(80))  # "llm:<model>" or "rules_fallback"
    validation: Mapped[dict] = mapped_column(JsonType, default=dict)
    safety: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    order_ref: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(60))
    details_redacted: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
