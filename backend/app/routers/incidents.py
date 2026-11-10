from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import constants as C
from .. import paypal, paypal_ops, report, service
from ..config import get_settings
from ..db import get_db
from ..models import AuditLog, Incident
from ..safety import evaluate
from ..schemas import EventIn, InvestigateIn, OpenIncidentIn, ResolveIn, event_dict, incident_dict, investigation_dict
from ..security import require_api_key
from ..timeutil import utcnow

router = APIRouter(dependencies=[Depends(require_api_key)])


def get_incident_or_404(db: Session, id_or_ref: str) -> Incident:
    inc = db.get(Incident, id_or_ref) or db.scalar(select(Incident).where(Incident.reference == id_or_ref.upper()))
    if inc is None:
        raise HTTPException(404, "Incident not found")
    return inc


@router.get("/incidents")
def list_incidents(
    status: str | None = None, type: str | None = Query(default=None, description="incident_type"), severity: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None, q: str | None = None, synthetic: bool | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
):
    conds = []
    if status:
        conds.append(Incident.status == status)
    if type:
        conds.append(Incident.incident_type == type)
    if severity:
        conds.append(Incident.severity == severity.capitalize())
    if date_from:
        conds.append(Incident.created_at >= date_from)
    if date_to:
        conds.append(Incident.created_at <= date_to)
    if synthetic is not None:
        conds.append(Incident.is_synthetic.is_(synthetic))
    if q:
        like = f"%{q}%"
        conds.append(or_(Incident.reference.ilike(like), Incident.order_id.ilike(like), Incident.capture_id.ilike(like), Incident.title.ilike(like)))
    total = db.scalar(select(func.count()).select_from(Incident).where(*conds)) or 0
    rows = db.scalars(select(Incident).where(*conds).order_by(Incident.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [incident_dict(i) for i in rows], "total": total}


@router.post("/incidents", status_code=201)
def open_incident(body: OpenIncidentIn, db: Session = Depends(get_db)):
    """The user submits a transaction ID (order ID, capture ID, or incident reference)."""
    tid = body.transaction_id.strip()
    inc = db.scalar(select(Incident).where(or_(Incident.reference == tid.upper(), Incident.order_id == tid, Incident.capture_id == tid)))
    if inc:
        return incident_dict(inc)
    order = service.get_order(db, tid)
    if order is None and get_settings().paypal_configured:
        paypal_ops.lookup_order(db, tid, actor="user")  # pulls authorized sandbox data; raises 4xx/5xx if PayPal can't find it
        order = service.get_order(db, tid)
    if order is None:
        raise HTTPException(404, "No evidence found for that transaction ID.")
    return incident_dict(service.open_incident(db, tid))


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: str, db: Session = Depends(get_db)):
    inc = get_incident_or_404(db, incident_id)
    a = service.analyze_order(db, inc.order_id)
    return {
        "incident": incident_dict(inc), "investigation": investigation_dict(service.latest_investigation(db, inc.id)),
        "safety": evaluate(a).model_dump(), "findings": [f.model_dump() for f in a.findings],
        "order": {"order_ref": inc.order_id, "payment_processing_count": (service.get_order(db, inc.order_id).payment_processing_count if service.get_order(db, inc.order_id) else 0)},
    }


@router.get("/incidents/{incident_id}/timeline")
def incident_timeline(incident_id: str, db: Session = Depends(get_db)):
    inc = get_incident_or_404(db, incident_id)
    a = service.analyze_order(db, inc.order_id)
    return {"incident_id": inc.id, "reference": inc.reference, "data_origin": "synthetic" if inc.is_synthetic else "sandbox",
            **a.model_dump(include={"order_ref", "authoritative_state", "local_state", "outcome_certainty", "distinct_completed_captures", "max_delivery_count"}),
            "entries": [e.model_dump() for e in a.entries], "findings": [f.model_dump() for f in a.findings]}


@router.post("/incidents/{incident_id}/events", status_code=201)
def ingest_event(incident_id: str, body: EventIn, db: Session = Depends(get_db)):
    """Ingest an authorized diagnostic event. Only synthetic events (demo) may claim PayPal as their source;
    everything else is stored as a merchant-log observation and is never treated as authoritative."""
    inc = get_incident_or_404(db, incident_id)
    if body.is_synthetic and not get_settings().enable_demo:
        raise HTTPException(403, "Synthetic events are disabled (ENABLE_DEMO=false).")
    source = body.source if (body.is_synthetic and body.source in C.SOURCES) else C.SOURCE_LOG
    res = service.record_event(
        db, order_ref=inc.order_id, source=source, event_type=body.event_type, timestamp=body.timestamp, external_event_id=body.external_event_id,
        http_status=body.http_status, error_code=body.error_code, transaction_state=body.transaction_state, correlation_id=body.correlation_id,
        capture_ref=body.capture_ref, verified=body.is_synthetic, is_synthetic=body.is_synthetic, payload=body.payload, actor="api")
    service.audit(db, actor="api", action="event_ingested", incident_id=inc.id, order_ref=inc.order_id,
                  details={"event_type": body.event_type, "source": source, "duplicate": not res.created})
    return {"event": event_dict(res.event), "created": res.created, "duplicate_ignored": not res.created}


@router.post("/incidents/{incident_id}/investigate")
def investigate(incident_id: str, body: InvestigateIn | None = None, db: Session = Depends(get_db)):
    body = body or InvestigateIn()
    inc = get_incident_or_404(db, incident_id)
    inv, a, safety = service.run_investigation(db, inc, actor=body.actor, use_llm=body.use_llm)
    db.refresh(inc)
    return {"incident": incident_dict(inc), "investigation": investigation_dict(inv), "safety": safety.model_dump(),
            "findings": [f.model_dump() for f in a.findings]}


@router.post("/incidents/{incident_id}/resolve")
def resolve(incident_id: str, body: ResolveIn, db: Session = Depends(get_db)):
    inc = get_incident_or_404(db, incident_id)
    inc.status, inc.resolved_at, inc.resolved_by, inc.resolution_note = "resolved", utcnow(), body.resolved_by, body.note
    service.audit(db, actor=body.resolved_by, action="incident_resolved", incident_id=inc.id, order_ref=inc.order_id, details={"note": body.note})
    db.refresh(inc)
    return incident_dict(inc)


@router.get("/incidents/{incident_id}/audit")
def incident_audit(incident_id: str, db: Session = Depends(get_db)):
    inc = get_incident_or_404(db, incident_id)
    rows = db.scalars(select(AuditLog).where(or_(AuditLog.incident_id == inc.id, AuditLog.order_ref == inc.order_id)).order_by(AuditLog.created_at)).all()
    return [{"id": r.id, "actor": r.actor, "action": r.action, "details": r.details_redacted, "created_at": r.created_at} for r in rows]


@router.get("/incidents/{incident_id}/report")
def incident_report(incident_id: str, format: str = Query("json", pattern="^(json|md|markdown)$"), db: Session = Depends(get_db)):
    inc = get_incident_or_404(db, incident_id)
    inv = service.latest_investigation(db, inc.id)
    if inv is None:
        raise HTTPException(409, "Run an investigation first (POST /api/incidents/{id}/investigate).")
    a = service.analyze_order(db, inc.order_id)
    safety = evaluate(a)
    if format == "json":
        return report.report_json(inc, inv, a, safety)
    service.audit(db, actor="user", action="report_exported", incident_id=inc.id, order_ref=inc.order_id, details={"format": format})
    return PlainTextResponse(report.report_markdown(inc, inv, a, safety), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{inc.reference}-report.md"'})
