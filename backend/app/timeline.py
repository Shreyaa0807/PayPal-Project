"""Timeline reconstruction (PRD FR-3). Pure functions: no LLM, no network, no DB writes.

Separates confirmed facts (recorded events) from inferred events (conclusions we draw from them),
and detects missing events, delayed notifications, duplicate deliveries and state mismatches.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from . import constants as C
from .config import get_settings
from .timeutil import as_utc, utcnow


class TimelineEntry(BaseModel):
    id: str
    timestamp: datetime
    event_type: str
    label: str
    source: str
    transaction_state: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    correlation_id: str | None = None
    capture_ref: str | None = None
    kind: Literal["confirmed", "unverified", "inferred"]
    authoritative: bool = False
    is_synthetic: bool = False
    delivery_count: int = 1
    flags: list[str] = Field(default_factory=list)
    detail: str | None = None


class Finding(BaseModel):
    code: str
    title: str
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)


class TimelineAnalysis(BaseModel):
    order_ref: str
    entries: list[TimelineEntry]
    findings: list[Finding]
    authoritative_state: str | None
    local_state: str | None
    outcome_certainty: Literal["confirmed", "unresolved", "conflicting"]
    completion_confirmed: bool
    failure_confirmed: bool
    has_timeout: bool
    capture_requested: bool
    verified_not_captured: bool
    local_mismatch: bool
    distinct_completed_captures: int
    max_delivery_count: int
    incident_type: str | None
    secondary_types: list[str]
    first_event_at: datetime | None
    last_event_at: datetime | None
    unresolved: bool
    has_authoritative_evidence: bool
    only_unverified_evidence: bool
    is_synthetic: bool
    amount: float | None = None
    currency: str | None = None
    error_codes: list[str] = Field(default_factory=list)

    def entry_ids(self) -> set[str]:
        return {e.id for e in self.entries}


LABELS = {
    C.ET_ORDER_CREATED: "Order created",
    C.ET_ORDER_APPROVED: "Buyer approved the order",
    C.ET_CAPTURE_REQUESTED: "Capture request submitted",
    C.ET_REQUEST_TIMEOUT: "Merchant request timed out (no response received)",
    C.ET_CAPTURE_COMPLETED: "Capture completed",
    C.ET_ORDER_COMPLETED: "Order completed",
    C.ET_CAPTURE_PENDING: "Capture is pending",
    C.ET_CAPTURE_DENIED: "Capture denied / declined",
    C.ET_CAPTURE_REFUNDED: "Capture refunded",
    C.ET_API_ERROR: "PayPal API returned an error",
    C.ET_STATE_LOOKUP: "Current state retrieved from PayPal",
    C.ET_OTHER: "Other event",
}
SOURCE_SUFFIX = {C.SOURCE_API: " (API response)", C.SOURCE_WEBHOOK: " (webhook)", C.SOURCE_LOG: " (merchant log)"}
FLAG_CODES = {"DUPLICATE_DELIVERY", "DELAYED_NOTIFICATION", "STATE_MISMATCH", "UNVERIFIED_EVENT", "API_ERROR", "CONFLICTING_EVIDENCE"}


def is_authoritative(e) -> bool:
    """Only PayPal-originated evidence counts as authoritative: a successful API response, or a signature-verified webhook."""
    if e.source == C.SOURCE_API:
        return e.http_status is None or 200 <= e.http_status < 300
    if e.source == C.SOURCE_WEBHOOK:
        return bool(e.verified)
    return False


def _label(e) -> str:
    if e.event_type == C.ET_LOCAL_STATE:
        return f"Local order record shows {e.transaction_state or 'unknown'} (merchant log)"
    base = LABELS.get(e.event_type, e.event_type.replace("_", " ").capitalize())
    return base + SOURCE_SUFFIX.get(e.source, "")


def _entry(e) -> TimelineEntry:
    auth = is_authoritative(e)
    kind = "unverified" if (e.source == C.SOURCE_WEBHOOK and not e.verified) else "confirmed"
    return TimelineEntry(
        id=e.id, timestamp=as_utc(e.timestamp), event_type=e.event_type, label=_label(e), source=e.source,
        transaction_state=e.transaction_state, http_status=e.http_status, error_code=e.error_code,
        correlation_id=e.correlation_id, capture_ref=e.capture_ref, kind=kind, authoritative=auth,
        is_synthetic=bool(e.is_synthetic), delivery_count=e.delivery_count or 1,
    )


def build_analysis(events: list, order=None, now: datetime | None = None) -> TimelineAnalysis:
    now = as_utc(now) if now else utcnow()
    evs = sorted(events, key=lambda e: (as_utc(e.timestamp), e.id))
    entries = [_entry(e) for e in evs]
    findings: list[Finding] = []
    by_id = {e.id: e for e in entries}

    auth_events = [e for e in evs if is_authoritative(e) and e.transaction_state]
    completions = [e for e in auth_events if C.state_class(e.transaction_state) == "success"]
    failures = [e for e in auth_events if C.state_class(e.transaction_state) == "failure"]
    refunds = [e for e in auth_events if (e.transaction_state or "").upper() in {"REFUNDED", "PARTIALLY_REFUNDED"}]
    timeouts = [e for e in evs if e.event_type == C.ET_REQUEST_TIMEOUT]
    requests = [e for e in evs if e.event_type == C.ET_CAPTURE_REQUESTED]
    local_events = [e for e in evs if e.event_type == C.ET_LOCAL_STATE]
    dup_events = [e for e in evs if (e.delivery_count or 1) > 1]
    error_events = [e for e in evs if e.source == C.SOURCE_API and (e.error_code or (e.http_status or 0) >= 400)]
    unverified = [e for e in evs if e.source == C.SOURCE_WEBHOOK and not e.verified]

    completion_confirmed = bool(completions)
    failure_confirmed = bool(failures)
    conflicting = completion_confirmed and failure_confirmed

    if conflicting:
        auth_state = "CONFLICT"
    elif refunds and auth_events and auth_events[-1] in refunds:
        auth_state = "REFUNDED"
    elif completion_confirmed:
        auth_state = "COMPLETED"
    elif failure_confirmed:
        auth_state = "DECLINED"
    elif auth_events:
        auth_state = (auth_events[-1].transaction_state or "").upper()
    else:
        auth_state = None

    outcome = "conflicting" if conflicting else ("confirmed" if (completion_confirmed or failure_confirmed) else "unresolved")

    local_state = (local_events[-1].transaction_state if local_events else None) or (order.local_state if order else None)
    lc, ac = C.state_class(local_state), C.state_class(auth_state)
    local_mismatch = bool(lc and ac and auth_state != "CONFLICT" and lc != ac)

    distinct_caps = {e.capture_ref for e in completions if e.capture_ref}
    distinct_completed = len(distinct_caps) if distinct_caps else (1 if completions else 0)

    last_req_ts = max([as_utc(e.timestamp) for e in requests + timeouts], default=None)
    lookups_after = [
        e for e in evs
        if e.event_type == C.ET_STATE_LOOKUP and is_authoritative(e) and last_req_ts and as_utc(e.timestamp) > last_req_ts
        and (e.transaction_state or "").upper() in {"APPROVED", "CREATED"}
    ]
    verified_not_captured = bool(requests and lookups_after and not completion_confirmed and not failure_confirmed)

    responded = bool(
        last_req_ts and any(
            as_utc(e.timestamp) >= min(as_utc(r.timestamp) for r in requests) and C.state_class(e.transaction_state) in {"success", "failure", "pending"}
            and e.event_type not in {C.ET_ORDER_CREATED, C.ET_ORDER_APPROVED}
            for e in auth_events
        )
    )
    thr = timedelta(seconds=get_settings().unresolved_after_seconds)
    stale_request = bool(requests and not responded and not timeouts and now - as_utc(requests[-1].timestamp) > thr)

    # ---- findings --------------------------------------------------------
    if conflicting:
        findings.append(Finding(code="CONFLICTING_EVIDENCE", title="Authoritative evidence conflicts",
                                detail="PayPal-originated events report both a successful and a failed outcome. A human must investigate before any action.",
                                evidence_ids=[e.id for e in completions + failures]))
    if timeouts and not (completion_confirmed or failure_confirmed):
        findings.append(Finding(code="UNKNOWN_OUTCOME_AFTER_TIMEOUT", title="Timeout left the payment outcome unknown",
                                detail="The merchant request timed out and no authoritative result followed. A timeout is not proof of failure.",
                                evidence_ids=[e.id for e in timeouts]))
    if stale_request:
        findings.append(Finding(code="NO_RESPONSE_RECORDED", title="Capture requested but no outcome recorded",
                                detail=f"No authoritative result has been recorded more than {get_settings().unresolved_after_seconds}s after the capture request.",
                                evidence_ids=[requests[-1].id]))
    if timeouts and completions:
        first_to = as_utc(timeouts[0].timestamp)
        late = [e for e in completions if as_utc(e.timestamp) >= first_to]
        if late:
            delay = int((as_utc(late[0].timestamp) - first_to).total_seconds())
            findings.append(Finding(code="DELAYED_NOTIFICATION", title="Confirmation arrived after the merchant timed out",
                                    detail=f"PayPal's completion evidence arrived {delay}s after the merchant request timed out.",
                                    evidence_ids=[timeouts[0].id, late[0].id]))
            findings.append(Finding(code="RESPONSE_LOST", title="Merchant likely missed the successful response",
                                    detail="The capture completed on PayPal's side although the merchant never received a success response.",
                                    evidence_ids=[timeouts[0].id, late[0].id]))
    if local_mismatch:
        auth_ids = [e.id for e in auth_events[-1:]]
        findings.append(Finding(code="STATE_MISMATCH", title="Local record disagrees with PayPal",
                                detail=f"Merchant record shows {local_state}, PayPal evidence shows {auth_state}.",
                                evidence_ids=([local_events[-1].id] if local_events else []) + auth_ids))
    if auth_state == "PENDING":
        findings.append(Finding(code="FINAL_EVENT_MISSING", title="No final completion or failure event received",
                                detail="PayPal reports the payment as PENDING. No evidence shows it succeeded or failed.",
                                evidence_ids=[auth_events[-1].id]))
    for e in dup_events:
        findings.append(Finding(code="DUPLICATE_DELIVERY", title="Repeated event delivery",
                                detail=f"Event {e.external_event_id} was delivered {e.delivery_count} times. Duplicate notifications do not by themselves prove duplicate charges.",
                                evidence_ids=[e.id]))
    if distinct_completed > 1:
        findings.append(Finding(code="POTENTIAL_DUPLICATE_CHARGE", title="Multiple distinct captures completed",
                                detail=f"{distinct_completed} different capture IDs completed for the same order.",
                                evidence_ids=[e.id for e in completions]))
    for e in error_events:
        findings.append(Finding(code="API_ERROR", title="PayPal API error", evidence_ids=[e.id],
                                detail=f"HTTP {e.http_status} {e.error_code or ''}".strip()))
    if unverified:
        findings.append(Finding(code="UNVERIFIED_EVENT", title="Webhook signature not verified",
                                detail="These webhook events were not signature-verified and are not trusted as evidence of payment state.",
                                evidence_ids=[e.id for e in unverified]))

    # ---- classification --------------------------------------------------
    types: list[str] = []
    if conflicting:
        types.append("conflicting_evidence")
    if distinct_completed > 1:
        types.append("potential_duplicate_charge")
    if timeouts and completion_confirmed:
        types.append("timeout_after_success")
    if (timeouts and not (completion_confirmed or failure_confirmed)) or stale_request:
        types.append("outcome_unknown")
    if local_mismatch:
        types.append("state_mismatch")
    if auth_state == "PENDING":
        types.append("payment_pending")
    if dup_events:
        types.append("duplicate_event_delivery")
    if error_events and not (completion_confirmed or failure_confirmed):
        types.append("api_error")
    primary = types[0] if types else None

    # ---- flags on entries ------------------------------------------------
    for f in findings:
        if f.code in FLAG_CODES:
            for eid in f.evidence_ids:
                if eid in by_id and f.code not in by_id[eid].flags:
                    by_id[eid].flags.append(f.code)

    # ---- inferred entries (clearly separated from confirmed facts) -------
    def _inferred(key: str, ts: datetime, label: str, detail: str, state: str | None = None) -> None:
        entries.append(TimelineEntry(id=f"inferred:{key}", timestamp=ts + timedelta(milliseconds=1), event_type="INFERRED", label=label,
                                     source="inference", transaction_state=state, kind="inferred", detail=detail,
                                     is_synthetic=any(e.is_synthetic for e in evs)))

    resp_lost = next((f for f in findings if f.code == "RESPONSE_LOST"), None)
    if resp_lost:
        _inferred("response_lost", as_utc(timeouts[0].timestamp), "Merchant never received/processed the capture response",
                  "Inferred from the timeout followed by an authoritative completion. Not directly observed.")
    if local_mismatch and ac in {"success", "failure"}:
        ts = as_utc(local_events[-1].timestamp) if local_events else now
        _inferred("local_not_reconciled", ts, "Local order record has not been reconciled with PayPal",
                  f"Inferred: merchant record ({local_state}) differs from PayPal ({auth_state}).")
    if auth_state == "PENDING":
        _inferred("no_final_event", as_utc(auth_events[-1].timestamp), "Final completion event not yet received",
                  "Inferred from the absence of a completion/failure event in the available evidence.")
    entries.sort(key=lambda e: e.timestamp)

    amount = order.amount if order and order.amount is not None else None
    ts_all = [as_utc(e.timestamp) for e in evs]
    return TimelineAnalysis(
        order_ref=order.order_ref if order else (evs[0].order_ref if evs else ""),
        entries=entries, findings=findings, authoritative_state=auth_state, local_state=local_state,
        outcome_certainty=outcome, completion_confirmed=completion_confirmed, failure_confirmed=failure_confirmed,
        has_timeout=bool(timeouts), capture_requested=bool(requests), verified_not_captured=verified_not_captured,
        local_mismatch=local_mismatch, distinct_completed_captures=distinct_completed,
        max_delivery_count=max([(e.delivery_count or 1) for e in evs], default=1),
        incident_type=primary, secondary_types=types[1:],
        first_event_at=min(ts_all, default=None), last_event_at=max(ts_all, default=None),
        unresolved=(outcome != "confirmed") or local_mismatch,
        has_authoritative_evidence=bool(auth_events),
        only_unverified_evidence=bool(unverified) and not auth_events,
        is_synthetic=any(e.is_synthetic for e in evs) or bool(order and order.is_synthetic),
        amount=amount, currency=(order.currency if order else None),
        error_codes=sorted({e.error_code for e in evs if e.error_code}),
    )
