"""Agentic state verification, simulated timeout, and AI log extraction (grounded + non-authoritative)."""
import json
from datetime import timedelta

from app import paypal, seed, service
from app.ai import llm, log_extract
from app.timeutil import utcnow

COMPLETED_ORDER = {"status": "COMPLETED", "purchase_units": [{"amount": {"value": "20.00", "currency_code": "USD"},
                   "payments": {"captures": [{"id": "CAP-REAL-1", "status": "COMPLETED"}]}}]}


def real_timeout_incident(db, ref="ORD-REAL-TO"):
    t = utcnow() - timedelta(minutes=10)
    kw = dict(order_ref=ref, refresh=False)
    service.record_event(db, source="paypal_api", event_type="ORDER_CREATED", transaction_state="CREATED", http_status=201, verified=True, timestamp=t, amount=20, **kw)
    service.record_event(db, source="merchant_log", event_type="CAPTURE_REQUESTED", timestamp=t + timedelta(seconds=30), **kw)
    service.record_event(db, source="merchant_log", event_type="REQUEST_TIMEOUT", timestamp=t + timedelta(seconds=31), **kw)
    service.record_event(db, source="merchant_log", event_type="LOCAL_STATE_RECORDED", transaction_state="PENDING", timestamp=t + timedelta(seconds=32), **kw)
    return service.refresh_order(db, ref)


# ---------------- agentic verification ----------------
def test_agent_verifies_real_order_state_before_diagnosing(db, settings, monkeypatch):
    monkeypatch.setattr(settings, "paypal_client_id", "id-xxxxxxxx")
    monkeypatch.setattr(settings, "paypal_client_secret", "secret-xxxxxxxx")
    inc = real_timeout_incident(db)
    assert inc.incident_type == "outcome_unknown"  # before the check: a timeout with no known outcome
    calls = []
    monkeypatch.setattr(paypal, "get_order", lambda oid: calls.append(oid) or paypal.PayPalResponse(200, COMPLETED_ORDER, "dbg-1"))
    inv, a, safety = service.run_investigation(db, inc, use_llm=False)
    assert calls == ["ORD-REAL-TO"]  # exactly one read-only GET
    steps = inv.validation["agent_steps"]
    assert steps[0]["step"] == "verify_state" and steps[0]["status"] == "ok" and "COMPLETED" in steps[0]["detail"]
    db.refresh(inc)
    assert inc.incident_type == "timeout_after_success"  # the verified state turned "unknown" into "success"
    assert inv.recommended_action["code"] == "RECONCILE_LOCAL_ORDER"
    assert "CAPTURE_AGAIN" in {p["code"] for p in inv.prohibited_actions}


def test_verification_failure_does_not_break_investigation(db, settings, monkeypatch):
    monkeypatch.setattr(settings, "paypal_client_id", "id-xxxxxxxx")
    monkeypatch.setattr(settings, "paypal_client_secret", "secret-xxxxxxxx")
    inc = real_timeout_incident(db, "ORD-REAL-FAIL")

    def boom(oid):
        raise paypal.PayPalTimeout("down")
    monkeypatch.setattr(paypal, "get_order", boom)
    inv, *_ = service.run_investigation(db, inc, use_llm=False)
    assert inv.validation["agent_steps"][0]["status"] == "failed"
    assert inv.recommended_action["code"] == "CHECK_CURRENT_STATE"  # still safe: outcome stays unknown


def test_synthetic_incidents_skip_the_live_check_and_say_so(db):
    inc = seed.run_scenario(db, "payment_pending")
    inv, *_ = service.run_investigation(db, inc, use_llm=False)
    assert inv.validation["agent_steps"][0]["status"] == "skipped" and "synthetic" in inv.validation["agent_steps"][0]["detail"]
    assert [s["step"] for s in inv.validation["agent_steps"]] == ["verify_state", "safety_rules", "ai_diagnosis"]


def test_verify_can_be_disabled(client):
    inc = client.post("/api/demo/scenarios/A").json()["incident"]
    out = client.post(f"/api/incidents/{inc['id']}/investigate", json={"use_llm": False, "verify": False}).json()
    assert [s["step"] for s in out["investigation"]["validation"]["agent_steps"]] == ["safety_rules", "ai_diagnosis"]


# ---------------- simulated slow network ----------------
def test_simulate_timeout_uses_short_read_timeout_and_is_recorded(client, db, monkeypatch):
    ref = "ORD-SIM-1"
    service.record_event(db, order_ref=ref, source="paypal_api", event_type="ORDER_CREATED", transaction_state="CREATED", http_status=201, verified=True, amount=10)
    seen = {}

    def slow(order_id, request_id, read_timeout=None):
        seen["rt"] = read_timeout
        raise paypal.PayPalTimeout("slow")
    monkeypatch.setattr(paypal, "capture_order", slow)
    r = client.post(f"/api/paypal/orders/{ref}/capture", json={"approved_by": "amna", "confirm": True, "simulate_timeout": True})
    assert r.json()["outcome"] == "timeout_unknown" and seen["rt"] == 0.3
    d = client.get(f"/api/transactions/{ref}").json()
    assert d["incident"]["incident_type"] == "outcome_unknown"


def test_simulate_timeout_blocked_when_demo_disabled(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "enable_demo", False)
    r = client.post("/api/paypal/orders/ORD-X/capture", json={"approved_by": "a", "confirm": True, "simulate_timeout": True})
    assert r.status_code == 403


# ---------------- AI log extraction ----------------
def sample_log(ts="2026-11-10 09:14"):
    return (f"{ts}:02 INFO  checkout: POST /v2/checkout/orders/ORD-1/capture submitted\n"
            f"{ts}:32 ERROR checkout: request timed out after 30000ms (ETIMEDOUT)\n"
            f"{ts}:40 WARN  orders: order ORD-1 left in PENDING\n"
            f"{ts}:41 DEBUG cache: warmed 12 keys\n")


def new_incident(client):
    return client.post("/api/demo/scenarios/B").json()["incident"]


def test_rules_extraction_without_llm(client):
    inc = new_incident(client)
    r = client.post(f"/api/incidents/{inc['id']}/logs", json={"log_text": sample_log(), "use_llm": False}).json()
    assert r["method"] == "rules" and r["ingested"] == 3 and not r["rejected"]
    assert [e["event_type"] for e in r["events"]] == ["CAPTURE_REQUESTED", "REQUEST_TIMEOUT", "LOCAL_STATE_RECORDED"]
    assert r["events"][2]["transaction_state"] == "PENDING"
    again = client.post(f"/api/incidents/{inc['id']}/logs", json={"log_text": sample_log(), "use_llm": False}).json()
    assert again["ingested"] == 0 and again["skipped_duplicates"] == 3  # re-pasting is idempotent


def test_pasted_log_can_never_change_the_confirmed_state(client):
    inc = new_incident(client)  # scenario B: PayPal says PENDING
    log = "2026-11-10 10:00:00 INFO  orders: order marked COMPLETED after manual check\n"
    r = client.post(f"/api/incidents/{inc['id']}/logs", json={"log_text": log, "use_llm": False}).json()
    assert r["ingested"] == 1
    tl = client.get(f"/api/incidents/{inc['id']}/timeline").json()
    assert tl["authoritative_state"] == "PENDING"  # merchant log is an observation, not proof
    pasted = [e for e in tl["entries"] if e["event_type"] == "LOCAL_STATE_RECORDED"][-1]
    assert pasted["authoritative"] is False and pasted["source"] == "merchant_log"


def test_llm_events_must_quote_the_log_hallucinations_are_rejected(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-1234567890")
    monkeypatch.setattr(settings, "openrouter_models", "model/a")
    inc = new_incident(client)
    log = sample_log()
    llm_out = {"events": [
        {"timestamp": "2026-11-10 09:14:32", "event_type": "REQUEST_TIMEOUT", "evidence_quote": "ERROR checkout: request timed out after 30000ms (ETIMEDOUT)"},
        {"timestamp": "2026-11-10 09:14:50", "event_type": "LOCAL_STATE_RECORDED", "transaction_state": "FAILED", "evidence_quote": "order ORD-1 marked FAILED by the fraud team"},
        {"timestamp": "2026-11-10 09:14:51", "event_type": "PAYMENT_SUCCEEDED", "evidence_quote": "warmed 12 keys"},
        {"event_type": "OTHER", "evidence_quote": "warmed 12 keys"},
    ]}
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: json.dumps(llm_out))
    r = client.post(f"/api/incidents/{inc['id']}/logs", json={"log_text": log}).json()
    assert r["method"] == "llm:model/a" and r["ingested"] == 1
    reasons = " | ".join(x["reason"] for x in r["rejected"])
    assert "quote not found" in reasons and "unsupported event_type" in reasons and "no parseable timestamp" in reasons


def test_llm_failure_falls_back_to_rules(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-1234567890")
    monkeypatch.setattr(settings, "openrouter_models", "model/a")

    def down(m, model):
        raise llm.LLMUnavailable("down")
    monkeypatch.setattr(llm, "call_openrouter", down)
    inc = new_incident(client)
    r = client.post(f"/api/incidents/{inc['id']}/logs", json={"log_text": sample_log()}).json()
    assert r["method"] == "rules" and r["ingested"] == 3 and r["notes"]


def test_log_is_redacted_before_it_reaches_the_llm(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-1234567890")
    monkeypatch.setattr(settings, "openrouter_models", "model/a")
    seen = {}
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: seen.setdefault("t", json.dumps(m)) and json.dumps({"events": []}))
    inc = new_incident(client)
    client.post(f"/api/incidents/{inc['id']}/logs", json={"log_text": "2026-11-10 09:00:00 INFO buyer jane@example.com paid with Bearer abc.def.ghi\n"})
    assert "jane@example.com" not in seen["t"] and "abc.def.ghi" not in seen["t"]


def test_log_extraction_endpoint_validates_input(client):
    inc = new_incident(client)
    assert client.post(f"/api/incidents/{inc['id']}/logs", json={"log_text": "x"}).status_code == 422
    assert client.post("/api/incidents/INC-NOPE00/logs", json={"log_text": "2026-11-10 09:00:00 something long enough"}).status_code == 404
