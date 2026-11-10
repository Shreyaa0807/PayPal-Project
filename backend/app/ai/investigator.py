"""AI investigation pipeline (PRD section 7, steps 5-7): investigate -> validate -> present.

Confidence = min(evidence-quality confidence from the rules, the LLM's stated confidence).
If the LLM is unavailable or fails validation on every model, the deterministic fallback is used.
"""
from __future__ import annotations

import json

from .. import constants as C
from ..config import get_settings
from ..redact import redact
from ..safety import SafetyResult
from ..timeline import TimelineAnalysis
from . import llm
from .error_docs import docs_for
from .fallback import build_fallback
from .prompts import build_messages
from .schemas import Diagnosis, InvestigationResult, ProhibitedActionItem
from .validator import validate_diagnosis

SAFE_PAYLOAD_KEYS = {"amount", "status", "reason", "timeout_ms", "note", "message", "order_status", "resource_status", "paypal_event_type"}


def build_context(a: TimelineAnalysis, s: SafetyResult, sev: dict, meta: dict) -> dict:
    entries = []
    for e in a.entries:
        entries.append({
            "id": e.id, "time": e.timestamp.isoformat(), "event": e.label, "source": e.source, "kind": e.kind,
            "authoritative": e.authoritative, "state": e.transaction_state, "http_status": e.http_status,
            "error_code": e.error_code, "flags": e.flags, "delivery_count": e.delivery_count, "detail": e.detail,
        })
    states = sorted({(e.transaction_state or "").upper() for e in a.entries if e.transaction_state})
    ctx = {
        "incident": {**meta, "classified_as": a.incident_type, "other_findings": a.secondary_types},
        "timeline": entries,
        "findings": [f.model_dump() for f in a.findings],
        "state": {"authoritative_state": a.authoritative_state, "local_state": a.local_state, "outcome_certainty": a.outcome_certainty,
                  "completed_captures": a.distinct_completed_captures},
        "rule_engine": {
            "confirmed_state": s.confirmed_state, "rules_triggered": [r.model_dump() for r in s.rules_triggered],
            "prohibited_actions": [p.model_dump() for p in s.prohibited_actions], "allowed_actions": s.allowed_actions,
            "baseline_recommendation": s.recommended_action.model_dump(), "evidence_confidence": s.evidence_confidence,
        },
        "severity": {"level": sev["level"], "reasons": sev["reasons"]},
        "documentation": docs_for(a.error_codes, states),
    }
    return redact(ctx)  # nothing sensitive ever reaches the LLM


def _min_conf(a: str, b: str) -> str:
    return C.CONFIDENCE_ORDER[min(C.CONFIDENCE_ORDER.index(a), C.CONFIDENCE_ORDER.index(b))]


def investigate(a: TimelineAnalysis, s: SafetyResult, sev: dict, meta: dict, use_llm: bool = True) -> InvestigationResult:
    settings = get_settings()
    validation: dict = {"passed": False, "fallback_used": False, "attempts": [], "fallback_reason": None}
    diag: Diagnosis | None = None
    generated_by = "rules_fallback"
    llm_conf: str | None = None

    if use_llm and settings.openrouter_api_key:
        context = build_context(a, s, sev, meta)
        context_text = json.dumps(context, default=str)
        messages = build_messages(context)
        for model in settings.llm_models:
            try:
                raw = llm.call_openrouter(messages, model)
            except llm.LLMUnavailable as e:
                validation["attempts"].append({"model": model, "result": "unavailable", "detail": str(e)})
                continue
            candidate, errors = validate_diagnosis(raw, a, s, context_text)
            if candidate is not None and not errors:
                diag, generated_by, llm_conf = candidate, f"llm:{model}", candidate.confidence
                validation["attempts"].append({"model": model, "result": "passed"})
                validation["passed"] = True
                break
            validation["attempts"].append({"model": model, "result": "rejected", "errors": errors})
        if diag is None:
            validation["fallback_reason"] = "LLM output unavailable or failed validation on all models"
    else:
        validation["fallback_reason"] = "LLM disabled or OPENROUTER_API_KEY not set" if not use_llm or not settings.openrouter_api_key else None

    if diag is None:
        diag = build_fallback(a, s)
        validation["fallback_used"] = True
        validation["passed"] = True  # the fallback is generated from rules + real evidence only

    # Confidence reflects evidence quality, not just the model's self-report.
    final_conf = _min_conf(s.evidence_confidence, diag.confidence)
    if s.evidence_confidence == "inconclusive":
        final_conf = "inconclusive"
        if "inconclusive" not in diag.root_cause.lower():
            diag.root_cause = "Inconclusive: " + diag.root_cause
        if not diag.missing_information:
            diag.missing_information = ["Authoritative PayPal evidence for this transaction."]
    diag.confidence = final_conf  # type: ignore[assignment]

    # The safety engine's prohibitions are authoritative; the model may only add known extras.
    merged = {p.code: ProhibitedActionItem(code=p.code, reason=p.reason) for p in s.prohibited_actions}
    for p in diag.prohibited_actions:
        if p.code in C.ACTION_CATALOG and p.code not in merged and p.code not in s.allowed_actions:
            merged[p.code] = p
    diag.prohibited_actions = list(merged.values())

    return InvestigationResult(diagnosis=diag, generated_by=generated_by, evidence_confidence=s.evidence_confidence,
                               llm_confidence=llm_conf, validation=validation)
