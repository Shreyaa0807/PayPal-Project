"""Explicit, explainable severity rules (PRD FR-6). Severity is NOT a fraud probability."""
from __future__ import annotations

from datetime import datetime

from . import constants as C
from .timeline import TimelineAnalysis
from .timeutil import as_utc, utcnow

NOTE = "Severity reflects operational and financial impact. It is not a fraud score or fraud probability."


def _band(value: float, bands: list[tuple[float, int]]) -> int:
    for threshold, points in bands:
        if value >= threshold:
            return points
    return 0


def compute_severity(a: TimelineAnalysis, affected_others: int = 0, now: datetime | None = None) -> dict:
    now = as_utc(now) if now else utcnow()
    factors: list[dict] = []

    def add(factor: str, points: int, detail: str) -> None:
        factors.append({"factor": factor, "points": points, "detail": detail})

    amount = a.amount or 0.0
    pts = _band(amount, [(1000, 3), (250, 2), (50, 1)])
    add("amount", pts, f"Amount {amount:.2f} {a.currency or ''}".strip())

    if a.outcome_certainty == "conflicting":
        add("state_uncertainty", 3, "Authoritative evidence conflicts")
    elif a.outcome_certainty == "unresolved" and a.incident_type is not None:
        add("state_uncertainty", 2, "Payment outcome is not confirmed by PayPal evidence")
    elif a.local_mismatch:
        add("state_uncertainty", 1, "Outcome confirmed, but the merchant record disagrees")
    else:
        add("state_uncertainty", 0, "Outcome confirmed and consistent")

    lc = C.state_class(a.local_state)
    retry_risk = bool(a.completion_confirmed and lc in {"pending", "failure"})
    add("retry_risk", 1 if retry_risk else 0,
        "Merchant record could trigger a duplicate payment attempt" if retry_risk else "No retry risk identified")

    if a.distinct_completed_captures > 1:
        add("potential_duplicate_charge", 4, f"{a.distinct_completed_captures} distinct captures completed for one order")
    else:
        add("potential_duplicate_charge", 0, "Only one capture completed (repeated notifications alone do not prove a duplicate charge)" if a.max_delivery_count > 1 else "No duplicate capture evidence")

    total_affected = affected_others + 1
    add("affected_transactions", _band(total_affected, [(10, 3), (3, 2), (2, 1)]),
        f"{total_affected} open incident(s) of the same type")

    hours = 0.0
    if a.unresolved and a.first_event_at:
        hours = max(0.0, (now - as_utc(a.first_event_at)).total_seconds() / 3600)
    add("unresolved_duration", _band(hours, [(24, 3), (6, 2), (1, 1)]), f"Unresolved for about {hours:.1f}h" if a.unresolved else "Not unresolved")

    score = sum(f["points"] for f in factors)
    level = "Critical" if score >= 8 else "High" if score >= 5 else "Medium" if score >= 3 else "Low"
    reasons = [f"{f['factor']}: {f['detail']} (+{f['points']})" for f in factors if f["points"]] or ["No risk factors identified (+0)"]
    return {"level": level, "score": score, "factors": factors, "reasons": reasons, "note": NOTE}
