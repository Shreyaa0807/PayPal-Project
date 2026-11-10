"""Repeatable evaluation over the seeded scenarios (PRD section 13 metrics).

Usage:  python -m scripts.run_eval            (rules-only)
        python -m scripts.run_eval --llm      (also uses OpenRouter if OPENROUTER_API_KEY is set)
Runs against an in-memory SQLite DB, never your real database.
"""
import os
import sys
import time

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("APP_ENV", "test")

from app.db import Base, SessionLocal, engine  # noqa: E402
from app import seed, service  # noqa: E402

EXPECTED = {
    "timeout_after_success": ("timeout_after_success", "RECONCILE_LOCAL_ORDER"),
    "payment_pending": ("payment_pending", "CHECK_CURRENT_STATE"),
    "duplicate_webhook": ("duplicate_event_delivery", "NO_ACTION_REQUIRED"),
    "state_mismatch": ("state_mismatch", "RECONCILE_LOCAL_ORDER"),
}


def main(use_llm: bool) -> int:
    Base.metadata.create_all(engine)
    correct = grounded = total_ev = unsafe = 0
    times = []
    with SessionLocal() as db:
        for name, (exp_type, exp_action) in EXPECTED.items():
            inc = seed.run_scenario(db, name)
            t0 = time.time()
            inv, a, safety = service.run_investigation(db, inc, use_llm=use_llm)
            times.append(time.time() - t0)
            ids = a.entry_ids()
            ok = inc.incident_type == exp_type and inv.recommended_action["code"] == exp_action
            correct += ok
            evs = inv.supporting_evidence + inv.contradictory_evidence
            total_ev += len(evs)
            grounded += sum(e["event_id"] in ids for e in evs)
            bad = inv.recommended_action["code"] in safety.prohibited_codes()
            unsafe += bad
            print(f"{name:24} type={inc.incident_type:26} action={inv.recommended_action['code']:22} conf={inv.confidence:8} by={inv.generated_by:30} {'OK' if ok and not bad else 'FAIL'}")
    n = len(EXPECTED)
    print(f"\nDiagnosis accuracy:        {correct}/{n}")
    print(f"Evidence-grounding rate:   {grounded}/{total_ev}")
    print(f"Unsafe recommendation rate:{unsafe}/{n}")
    print(f"Avg time to investigation: {sum(times) / len(times):.2f}s")
    print("\nNote: 4 scripted scenarios are a smoke test, not proof of production reliability.")
    return 0 if correct == n and unsafe == 0 and grounded == total_ev else 1


if __name__ == "__main__":
    sys.exit(main("--llm" in sys.argv))
