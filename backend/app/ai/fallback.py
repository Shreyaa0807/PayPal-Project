"""Rules-only diagnosis used when the LLM is unavailable or its output fails validation.

Fully deterministic, so the product still fails safe (PRD: 'Fail safely when ... the AI service is unavailable').
"""
from __future__ import annotations

from ..safety import SafetyResult
from ..timeline import TimelineAnalysis
from .schemas import Diagnosis, EvidenceItem, ProhibitedActionItem, RecommendedAction


def _find(a: TimelineAnalysis, *, event_type: str | None = None, state: str | None = None, authoritative: bool | None = None,
          inferred: bool = False, source: str | None = None):
    for e in reversed(a.entries):
        if (e.kind == "inferred") != inferred:
            continue
        if event_type and e.event_type != event_type:
            continue
        if state and (e.transaction_state or "").upper() != state:
            continue
        if authoritative is not None and e.authoritative != authoritative:
            continue
        if source and e.source != source:
            continue
        return e
    return None


def _ev(entry, statement: str) -> list[EvidenceItem]:
    return [EvidenceItem(event_id=entry.id, statement=statement)] if entry else []


def build_fallback(a: TimelineAnalysis, s: SafetyResult) -> Diagnosis:
    t = a.incident_type
    rec = RecommendedAction(code=s.recommended_action.code, explanation=s.recommended_action.explanation)
    prohibited = [ProhibitedActionItem(code=p.code, reason=p.reason) for p in s.prohibited_actions]
    conf = s.evidence_confidence
    timeout = _find(a, event_type="REQUEST_TIMEOUT")
    request = _find(a, event_type="CAPTURE_REQUESTED")
    local = _find(a, event_type="LOCAL_STATE_RECORDED")
    completed = _find(a, state="COMPLETED", authoritative=True)
    pending = _find(a, state="PENDING", authoritative=True)
    dup = next((e for e in a.entries if e.delivery_count > 1), None)
    inferred_lost = _find(a, inferred=True)

    if t == "timeout_after_success":
        return Diagnosis(
            summary="The capture request timed out from the merchant's side, but PayPal-originated evidence confirms the capture completed. The local order record does not reflect this.",
            root_cause="The merchant application most likely missed the successful capture response (timeout) and never updated its local order from the later confirmation.",
            supporting_evidence=_ev(timeout, "The merchant's request timed out, so no response was received.") + _ev(completed, "A PayPal-originated event confirms the capture COMPLETED.") + _ev(local, f"The local record shows {a.local_state}, not COMPLETED."),
            contradictory_evidence=[], confidence=conf, recommended_action=rec, prohibited_actions=prohibited,
            alternative_explanations=["The completion webhook was received but the merchant's handler failed to update the order.",
                                      "A slow order-state update rather than a lost response."],
            missing_information=["Merchant server logs showing whether the webhook handler ran.", "Why the original request exceeded the timeout."])
    if t == "payment_pending":
        return Diagnosis(
            summary="The payment is PENDING. PayPal has not yet reported a final success or failure, and none can be assumed.",
            root_cause="Unresolved payment state: the evidence confirms PENDING but not the final outcome. There is no evidence that the payment failed or succeeded.",
            supporting_evidence=_ev(pending, "PayPal evidence reports the capture as PENDING.") + _ev(request, "A capture was requested earlier.") + _ev(local, f"The local record shows {a.local_state}."),
            contradictory_evidence=[], confidence=conf, recommended_action=rec, prohibited_actions=prohibited,
            alternative_explanations=["The final completion event is delayed and will arrive later.", "A completion event was sent but not delivered or not verified."],
            missing_information=["The current state retrieved directly from PayPal.", "The reason PayPal reports the payment as pending (not present in the evidence).", "Whether the webhook endpoint is receiving events."])
    if t == "duplicate_event_delivery":
        n = dup.delivery_count if dup else a.max_delivery_count
        return Diagnosis(
            summary=f"The same payment-completion event was delivered {n} times. It was recorded and processed once; duplicate notifications do not by themselves prove duplicate charges.",
            root_cause="Repeated delivery of one event ID, which is consistent with at-least-once webhook delivery. Deduplication by event ID prevented duplicate internal processing.",
            supporting_evidence=_ev(dup, f"Event {dup.id if dup else ''} was delivered {n} times under a single event ID.") + _ev(completed, f"{a.distinct_completed_captures} distinct capture completed for this order."),
            contradictory_evidence=[], confidence=conf, recommended_action=rec, prohibited_actions=prohibited,
            alternative_explanations=["The receiving endpoint was slow to acknowledge, so the sender re-delivered."],
            missing_information=["Webhook endpoint acknowledgement latency logs."] if conf != "high" else [])
    if t == "state_mismatch":
        return Diagnosis(
            summary=f"The merchant's record ({a.local_state}) disagrees with PayPal's confirmed state ({a.authoritative_state}).",
            root_cause="The local order was not reconciled with PayPal's authoritative state. The exact reason cannot be determined from the available evidence.",
            supporting_evidence=_ev(completed or pending, f"PayPal evidence shows {a.authoritative_state}.") + _ev(local, f"The merchant record shows {a.local_state}."),
            contradictory_evidence=[], confidence=conf, recommended_action=rec, prohibited_actions=prohibited,
            alternative_explanations=["A merchant-side parsing or update failure.", "A race between webhook processing and the local update."],
            missing_information=["Merchant application logs around the time of the local update."])
    if t == "outcome_unknown":
        return Diagnosis(
            summary="The payment outcome is unknown: no authoritative result was recorded after the request.",
            root_cause="Inconclusive. A missing response does not show failure; the request may have succeeded.",
            supporting_evidence=_ev(timeout or request, "The request produced no recorded result."),
            contradictory_evidence=[], confidence="low" if a.has_authoritative_evidence else "inconclusive", recommended_action=rec, prohibited_actions=prohibited,
            alternative_explanations=["The payment succeeded and the response was lost.", "The request never reached PayPal."],
            missing_information=["The current order/capture state retrieved from PayPal."])
    if t == "conflicting_evidence":
        return Diagnosis(
            summary="Authoritative evidence conflicts about the payment outcome.",
            root_cause="Conflicting evidence: PayPal-originated events report both success and failure. A human must investigate.",
            supporting_evidence=[EvidenceItem(event_id=i, statement="Part of the conflicting evidence.") for f in a.findings if f.code == "CONFLICTING_EVIDENCE" for i in f.evidence_ids],
            contradictory_evidence=[], confidence="low", recommended_action=rec, prohibited_actions=prohibited,
            alternative_explanations=["Different captures on the same order.", "An out-of-order event delivery."],
            missing_information=["Current state from PayPal for each capture ID."])
    if t == "potential_duplicate_charge":
        caps = [e for e in a.entries if e.authoritative and e.transaction_state == "COMPLETED" and e.capture_ref]
        return Diagnosis(
            summary=f"{a.distinct_completed_captures} different capture IDs completed for the same order, which may indicate a duplicate charge.",
            root_cause="Multiple distinct captures completed. The evidence shows this happened but not why; a human must review it.",
            supporting_evidence=[EvidenceItem(event_id=e.id, statement=f"Capture {e.capture_ref} completed.") for e in caps[:4]],
            contradictory_evidence=[], confidence=conf, recommended_action=rec, prohibited_actions=prohibited,
            alternative_explanations=["Intentional partial captures.", "A retry without an idempotency key."],
            missing_information=["Whether the captures were intentional and their amounts."])
    if t == "api_error":
        err = next((e for e in a.entries if e.error_code), None)
        return Diagnosis(
            summary=f"PayPal returned an API error ({a.error_codes[0] if a.error_codes else 'unknown'}) and the payment outcome is not confirmed.",
            root_cause="An API error was returned. The final payment state must be verified before any retry.",
            supporting_evidence=_ev(err, "PayPal returned an error response."),
            contradictory_evidence=[], confidence="low" if a.has_authoritative_evidence else "inconclusive", recommended_action=rec, prohibited_actions=prohibited,
            alternative_explanations=[], missing_information=["The current state from PayPal."])
    return Diagnosis(
        summary="No payment incident could be diagnosed from the available evidence.",
        root_cause="Inconclusive: the evidence is insufficient to explain a problem.",
        supporting_evidence=[], contradictory_evidence=[], confidence="inconclusive", recommended_action=rec, prohibited_actions=prohibited,
        alternative_explanations=[], missing_information=["PayPal API responses or webhook events for this transaction."])
