"""Step 6 of the pipeline: validate the AI output before anyone sees it.

Checks: JSON schema, real evidence references, no prohibited action (by code OR by wording),
no invented identifiers (error codes / states / event names), and honest confidence.
"""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from .. import constants as C
from ..safety import SafetyResult
from ..timeline import TimelineAnalysis
from .schemas import Diagnosis

NEGATION = re.compile(r"(do not|don't|dont|never|avoid|without|must not|should not|shouldn't|not to|instead of|rather than|no need to|cannot|can't|block)", re.I)

PROHIBITED_WORDING = {
    C.CAPTURE_AGAIN: r"(capture\s+(it|the payment|the order|the funds)?\s*again|re-?capture|(new|another|second)\s+capture|retry\s+(the\s+)?capture|capture\s+retry)",
    C.RETRY_PAYMENT: r"(retry\s+(the|this)\s+(payment|charge|transaction)|re-?submit\s+(the\s+)?(payment|charge)|charge\s+(the\s+)?(buyer|customer|card)\s+again|resend\s+the\s+payment)",
    C.CREATE_NEW_ORDER: r"((create|place|issue|start|open)\s+(a\s+)?(new|another|replacement)\s+order)",
    C.REFUND: r"\brefund",
    C.MARK_ORDER_FAILED: r"mark\s+(the\s+)?(order|payment|transaction)\s+as\s+(failed|unsuccessful|declined)",
}
CODE_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("response is not JSON")
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("JSON root is not an object")
    return data


def violates_by_wording(text: str, code: str) -> bool:
    pattern = PROHIBITED_WORDING.get(code)
    if not pattern:
        return False
    for m in re.finditer(pattern, text, re.I):
        window = text[max(0, m.start() - 45): m.start()]
        if not NEGATION.search(window):
            return True
    return False


def validate_diagnosis(raw_text: str, analysis: TimelineAnalysis, safety: SafetyResult, context_text: str) -> tuple[Diagnosis | None, list[str]]:
    errors: list[str] = []
    try:
        data = extract_json(raw_text)
        diag = Diagnosis.model_validate(data)
    except (ValueError, ValidationError) as e:
        return None, [f"schema: {str(e)[:300]}"]

    valid_ids = analysis.entry_ids()
    for field, items in (("supporting_evidence", diag.supporting_evidence), ("contradictory_evidence", diag.contradictory_evidence)):
        for it in items:
            if it.event_id not in valid_ids:
                errors.append(f"{field}: unknown event_id '{it.event_id}'")
    if diag.confidence != "inconclusive" and not diag.supporting_evidence:
        errors.append("supporting_evidence is empty for a non-inconclusive diagnosis")

    code = diag.recommended_action.code
    if code not in C.ACTION_CATALOG:
        errors.append(f"recommended_action.code '{code}' is not a known action")
    if code in safety.prohibited_codes():
        errors.append(f"recommended_action.code '{code}' is prohibited by the safety engine")
    elif code not in safety.allowed_actions:
        errors.append(f"recommended_action.code '{code}' is not in the allowed actions")
    for pcode in safety.prohibited_codes():
        if violates_by_wording(diag.recommended_action.explanation, pcode):
            errors.append(f"recommended_action text appears to recommend prohibited action {pcode}")

    allowed_tokens = set(CODE_TOKEN.findall(context_text)) | set(C.ACTION_CATALOG) | C.ALL_STATES
    text_blob = " ".join([diag.summary, diag.root_cause, diag.recommended_action.explanation,
                          *[e.statement for e in diag.supporting_evidence + diag.contradictory_evidence],
                          *diag.alternative_explanations, *diag.missing_information])
    invented = sorted({t for t in CODE_TOKEN.findall(text_blob) if t not in allowed_tokens})
    if invented:
        errors.append(f"ungrounded identifiers (not in evidence): {', '.join(invented[:5])}")
    return diag, errors
