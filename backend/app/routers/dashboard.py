from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, Incident, Order
from ..schemas import incident_dict, order_dict
from ..security import require_api_key
from .. import service
from ..safety import evaluate

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/dashboard/summary")
def summary(db: Session = Depends(get_db)):
    count = lambda *c: db.scalar(select(func.count()).select_from(Order).where(*c)) or 0  # noqa: E731
    open_inc = Incident.status != "resolved"
    by_sev = dict(db.execute(select(Incident.severity, func.count()).where(open_inc).group_by(Incident.severity)).all())
    by_type = dict(db.execute(select(Incident.incident_type, func.count()).where(open_inc).group_by(Incident.incident_type)).all())
    dup_orders = [o for o in db.scalars(select(Order).where(Order.payment_processing_count > 1)).all()]
    recent = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)).all()
    return {
        "transactions": {
            "total": count(), "completed": count(Order.status == "completed"), "failed": count(Order.status == "failed"),
            "pending": count(Order.status == "pending"), "synthetic": count(Order.is_synthetic.is_(True)), "sandbox": count(Order.is_synthetic.is_(False)),
        },
        "incidents": {
            "open": db.scalar(select(func.count()).select_from(Incident).where(open_inc)) or 0,
            "resolved": db.scalar(select(func.count()).select_from(Incident).where(Incident.status == "resolved")) or 0,
            "by_severity": {s: by_sev.get(s, 0) for s in ("Critical", "High", "Medium", "Low")},
            "by_type": by_type,
        },
        "duplicate_payment_warnings": [{"order_ref": o.order_ref, "captures_completed": o.payment_processing_count, "data_origin": "synthetic" if o.is_synthetic else "sandbox"} for o in dup_orders],
        "high_priority_incidents": [incident_dict(i) for i in db.scalars(
            select(Incident).where(open_inc, Incident.severity.in_(["Critical", "High"])).order_by(Incident.created_at.desc()).limit(5))],
        "recent_activity": [{"actor": r.actor, "action": r.action, "incident_id": r.incident_id, "order_ref": r.order_ref, "created_at": r.created_at} for r in recent],
        "note": "Incidents flagged data_origin='synthetic' are seeded demonstration data, not real sandbox transactions.",
    }


@router.get("/transactions/search")
def search(q: str, db: Session = Depends(get_db)):
    like = f"%{q}%"
    orders = db.scalars(select(Order).where(or_(Order.order_ref.ilike(like), Order.capture_id.ilike(like))).order_by(Order.created_at.desc()).limit(25)).all()
    refs = {o.order_ref for o in orders}
    for inc in db.scalars(select(Incident).where(Incident.reference.ilike(like)).limit(25)):
        if inc.order_id not in refs and (o := service.get_order(db, inc.order_id)):
            orders.append(o)
            refs.add(o.order_ref)
    return {"items": [{**order_dict(o), "incident": (incident_dict(i) if (i := service.latest_incident(db, o.order_ref)) else None)} for o in orders]}


@router.get("/transactions/{ref}")
def transaction_detail(ref: str, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    order = service.get_order(db, ref) or db.scalar(select(Order).where(Order.capture_id == ref))
    if order is None:
        inc = db.scalar(select(Incident).where(Incident.reference == ref.upper()))
        order = service.get_order(db, inc.order_id) if inc else None
    if order is None:
        raise HTTPException(404, "Transaction not found")
    a = service.analyze_order(db, order.order_ref)
    inc = service.latest_incident(db, order.order_ref)
    return {"order": order_dict(order), "incident": incident_dict(inc) if inc else None, "safety": evaluate(a).model_dump(),
            "timeline": [e.model_dump() for e in a.entries], "findings": [f.model_dump() for f in a.findings]}
