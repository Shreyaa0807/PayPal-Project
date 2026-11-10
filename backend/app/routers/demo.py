from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import seed, service
from ..db import get_db
from ..schemas import incident_dict, investigation_dict
from ..safety import evaluate
from ..security import require_demo

router = APIRouter(dependencies=[Depends(require_demo)])


@router.get("/demo/scenarios")
def list_scenarios():
    return [{"id": k, "alias": v["alias"], "title": v["title"], "data_origin": "synthetic"} for k, v in seed.SCENARIOS.items()]


@router.post("/demo/scenarios/{scenario}", status_code=201)
def trigger(scenario: str, investigate: bool = False, use_llm: bool = True, db: Session = Depends(get_db)):
    """Trigger a seeded SYNTHETIC scenario (A/B/C/D or its name). Optionally run the investigation right away."""
    name = seed.resolve_scenario(scenario)
    if name is None:
        raise HTTPException(404, f"Unknown scenario. Use one of: {', '.join(seed.SCENARIOS)} (or A-D).")
    inc = seed.run_scenario(db, name)
    out = {"scenario": name, "data_origin": "synthetic", "incident": incident_dict(inc)}
    if investigate:
        inv, a, safety = service.run_investigation(db, inc, actor="demo", use_llm=use_llm)
        db.refresh(inc)
        out.update(incident=incident_dict(inc), investigation=investigation_dict(inv), safety=safety.model_dump())
    return out


@router.post("/demo/seed-all", status_code=201)
def seed_all(db: Session = Depends(get_db)):
    return {"incidents": [incident_dict(seed.run_scenario(db, n)) for n in seed.SCENARIOS]}


@router.delete("/demo/reset")
def reset(db: Session = Depends(get_db)):
    """Delete synthetic data only. Real sandbox transactions are never touched."""
    return {"deleted_synthetic_incidents": seed.reset_synthetic(db)}
