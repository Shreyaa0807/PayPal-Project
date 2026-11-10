"""Deterministic payment safety engine (PRD FR-5). Mandatory, independent of the LLM.

It decides: the confirmed payment state, which recovery actions are PROHIBITED, which are allowed,
and a baseline recommended action. The AI's output is validated against these results.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from . import constants as C
from .timeline import TimelineAnalysis


class RuleHit(BaseModel):
    rule_id: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)


class ProhibitedAction(BaseModel):
    code: str
    reason: str
    rule_id: str


class RecommendedAction(BaseModel):
    code: str
    explanation: str


class SafetyResult(BaseModel):
    confirmed_state: str  # COMPLETED | DECLINED | PENDING | UNKNOWN | CONFLICT | REFUNDED
    outcome_certainty: str
    rules_triggered: list[RuleHit]
    prohibited_actions: list[ProhibitedAction]
    allowed_actions: list[str]
    recommended_action: RecommendedAction
    requires_human_approval: bool = True
    needs_further_investigation: bool = False
    warnings: list[str] = Field(default_factory=list)
    evidence_confidence: str = "inconclusive"

    def prohibited_codes(self) -> set[str]:
        return {p.code for p in self.prohibited_actions}


def evidence_confidence(a: TimelineAnalysis) -> str:
    """Confidence derived from the QUALITY of evidence, not from the LLM's self-reported certainty."""
    if not a.has_authoritative_evidence:
        return "inconclusive"
    if a.outcome_certainty == "conflicting":
        return "low"
    if a.outcome_certainty == "confirmed":
        return "high"
    if a.authoritative_state == "PENDING":
        return "medium"  # we are sure it is pending, but not what the final outcome is
    return "low"


def can_capture(a: TimelineAnalysis) -> tuple[bool, str]:
    """Gate used before the backend submits a capture to PayPal."""
    if a.outcome_certainty == "conflicting":
        return False, "Evidence conflicts; investigate before any capture."
    if a.completion_confirmed:
        return False, "A capture is already confirmed as completed for this order. Capturing again could double charge."
    if a.capture_requested and not a.verified_not_captured:
        return False, "A capture was already requested and its outcome is not verified. Retrieve the current order state from PayPal first."
    return True, "ok"


def evaluate(a: TimelineAnalysis) -> SafetyResult:
    rules: list[RuleHit] = []
    prohibited: dict[str, ProhibitedAction] = {}
    warnings: list[str] = []

    def prohibit(code: str, rule_id: str, reason: str) -> None:
        prohibited.setdefault(code, ProhibitedAction(code=code, reason=reason, rule_id=rule_id))

    def ids(*codes: str) -> list[str]:
        out: list[str] = []
        for f in a.findings:
            if f.code in codes:
                out += f.evidence_ids
        return out or [e.id for e in a.entries if e.authoritative][-1:]

    state = a.authoritative_state
    confirmed_state = state if state in {"COMPLETED", "DECLINED", "PENDING", "CONFLICT", "REFUNDED"} else "UNKNOWN"
    allowed = {C.CHECK_CURRENT_STATE, C.ESCALATE_FOR_REVIEW, C.REQUEST_MORE_EVIDENCE}
    needs_investigation = False

    # Rule order = precedence for the recommended action.
    if a.outcome_certainty == "conflicting":  # R4
        rules.append(RuleHit(rule_id="R4", description="Evidence conflicts -> flag for further investigation; block all consequential actions.", evidence_ids=ids("CONFLICTING_EVIDENCE")))
        for code in (C.CAPTURE_AGAIN, C.RETRY_PAYMENT, C.CREATE_NEW_ORDER, C.MARK_ORDER_FAILED):
            prohibit(code, "R4", "Authoritative evidence conflicts; a human must verify the true state first.")
        needs_investigation = True
        rec = (C.ESCALATE_FOR_REVIEW, "Evidence conflicts. Escalate to a human and retrieve the current state from PayPal before any action.")
    elif a.completion_confirmed:  # R1
        success_ids = [e.id for e in a.entries if e.authoritative and C.state_class(e.transaction_state) == "success"][:1]
        rules.append(RuleHit(rule_id="R1", description="Capture completion is confirmed -> block any new capture, retry or replacement order.", evidence_ids=success_ids))
        for code in (C.CAPTURE_AGAIN, C.RETRY_PAYMENT, C.CREATE_NEW_ORDER):
            prohibit(code, "R1", "PayPal evidence confirms the payment already completed; repeating it risks a duplicate charge.")
        prohibit(C.MARK_ORDER_FAILED, "R7", "Evidence shows the payment succeeded; marking the order failed would be wrong.")
        if a.distinct_completed_captures > 1:
            rec = (C.ESCALATE_FOR_REVIEW, "Multiple distinct captures completed for one order. Escalate to a human to review the duplicate; any refund needs explicit human approval.")
        elif a.local_mismatch or a.has_timeout:
            allowed |= {C.RECONCILE_LOCAL_ORDER}
            rec = (C.RECONCILE_LOCAL_ORDER, "Reconcile the local order with PayPal's confirmed COMPLETED state. Do not initiate a new capture.")
        else:
            allowed |= {C.NO_ACTION_REQUIRED}
            rec = (C.NO_ACTION_REQUIRED, "Payment is confirmed completed and the local record agrees. Keep deduplicating events by ID; do not capture again.")
    elif a.authoritative_state == "PENDING":  # R2
        rules.append(RuleHit(rule_id="R2", description="Payment is pending -> check its current state before any retry.", evidence_ids=ids("FINAL_EVENT_MISSING")))
        for code in (C.CAPTURE_AGAIN, C.RETRY_PAYMENT, C.CREATE_NEW_ORDER):
            prohibit(code, "R2", "The payment is still pending; a retry could create a duplicate attempt.")
        prohibit(C.MARK_ORDER_FAILED, "R7", "A pending payment is not a failed payment.")
        allowed |= {C.WAIT_AND_RECHECK}
        rec = (C.CHECK_CURRENT_STATE, "Retrieve the current order/capture state from PayPal, then wait and re-check. Do not claim success or failure without evidence.")
    elif a.failure_confirmed:  # confirmed decline
        rules.append(RuleHit(rule_id="R5", description="Failure confirmed by PayPal -> a retry is possible only with human approval and PayPal-Request-Id idempotency.", evidence_ids=ids("API_ERROR")))
        prohibit(C.RETRY_PAYMENT, "R5", "Blind retries are never allowed; use the approval-gated, idempotent retry instead.")
        prohibit(C.REFUND, "R6", "Nothing was captured, so there is nothing to refund.")
        allowed |= {C.RETRY_WITH_APPROVAL}
        rec = (C.ESCALATE_FOR_REVIEW, "PayPal confirms the payment did not complete. A new attempt needs buyer action and explicit human approval, using a PayPal-Request-Id.")
    elif a.has_timeout or any(f.code == "NO_RESPONSE_RECORDED" for f in a.findings):  # R3
        rules.append(RuleHit(rule_id="R3", description="Outcome unknown after a timeout/no response -> retrieve current state before any new payment attempt.", evidence_ids=ids("UNKNOWN_OUTCOME_AFTER_TIMEOUT", "NO_RESPONSE_RECORDED")))
        for code in (C.CAPTURE_AGAIN, C.RETRY_PAYMENT, C.CREATE_NEW_ORDER):
            prohibit(code, "R3", "The outcome is unknown; the original request may have succeeded.")
        prohibit(C.MARK_ORDER_FAILED, "R7", "A missing response is not proof of failure.")
        if a.verified_not_captured:
            rules.append(RuleHit(rule_id="R5", description="PayPal confirms the order was not captured -> an idempotent retry is possible with human approval.", evidence_ids=[]))
            allowed |= {C.RETRY_WITH_APPROVAL}
            rec = (C.RETRY_WITH_APPROVAL, "PayPal shows the order is not captured. A retry is possible only with human approval and the same PayPal-Request-Id.")
        else:
            rec = (C.CHECK_CURRENT_STATE, "Retrieve the current order/capture state from PayPal before any new payment attempt.")
        needs_investigation = True
    elif not a.has_authoritative_evidence:
        rules.append(RuleHit(rule_id="R8", description="No authoritative evidence -> the outcome cannot be determined.", evidence_ids=[]))
        for code in (C.CAPTURE_AGAIN, C.RETRY_PAYMENT, C.CREATE_NEW_ORDER, C.MARK_ORDER_FAILED):
            prohibit(code, "R8", "There is no authoritative evidence of the payment state.")
        needs_investigation = True
        rec = (C.REQUEST_MORE_EVIDENCE, "Collect PayPal API/webhook evidence for this transaction before acting.")
    else:
        for code in (C.RETRY_PAYMENT,):
            prohibit(code, "R5", "Blind retries are never allowed.")
        rec = (C.CHECK_CURRENT_STATE, "Retrieve the current state from PayPal to confirm before acting.")

    # Rules that always apply
    prohibit(C.REFUND, "R6", "Refunds are out of scope for the MVP and always require explicit human approval.")
    if a.max_delivery_count > 1:
        rules.append(RuleHit(rule_id="R9", description="Repeated webhook delivery -> process the event once (idempotent); duplicates do not prove duplicate charges.", evidence_ids=ids("DUPLICATE_DELIVERY")))
    if a.distinct_completed_captures > 1:
        warnings.append("Potential duplicate charge: multiple distinct captures completed for one order.")
        needs_investigation = True
    if a.only_unverified_evidence:
        warnings.append("Only unverified webhook events are present; they are not treated as evidence of payment state.")
    rules.append(RuleHit(rule_id="R6", description="Any consequential payment operation requires explicit human approval and is audit-logged.", evidence_ids=[]))
    warnings.append("A missing response is never treated as proof that a payment failed.")

    code, explanation = rec
    return SafetyResult(
        confirmed_state=confirmed_state, outcome_certainty=a.outcome_certainty, rules_triggered=rules,
        prohibited_actions=list(prohibited.values()), allowed_actions=sorted(allowed),
        recommended_action=RecommendedAction(code=code, explanation=explanation),
        needs_further_investigation=needs_investigation, warnings=warnings,
        evidence_confidence=evidence_confidence(a),
    )
