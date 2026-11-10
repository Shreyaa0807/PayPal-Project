import json

import pytest

from app import paypal, seed, service
from app.redact import redact, redact_text
from app.safety import can_capture, evaluate


# ---------------- safety engine ----------------
def test_completed_blocks_capture_again(db):
    inc = seed.run_scenario(db, "timeout_after_success")
    a = service.analyze_order(db, inc.order_id)
    s = evaluate(a)
    assert "CAPTURE_AGAIN" in s.prohibited_codes() and "MARK_ORDER_FAILED" in s.prohibited_codes()
    assert can_capture(a)[0] is False


def test_timeout_without_outcome_is_never_failure(db):
    ref = "ORD-TO-1"
    service.record_event(db, order_ref=ref, source="paypal_api", event_type="ORDER_CREATED", transaction_state="CREATED", http_status=201, verified=True, amount=10)
    service.record_event(db, order_ref=ref, source="merchant_log", event_type="CAPTURE_REQUESTED")
    service.record_event(db, order_ref=ref, source="merchant_log", event_type="REQUEST_TIMEOUT")
    a = service.analyze_order(db, ref)
    s = evaluate(a)
    assert a.incident_type == "outcome_unknown" and s.confirmed_state == "UNKNOWN"
    assert {"CAPTURE_AGAIN", "RETRY_PAYMENT", "CREATE_NEW_ORDER", "MARK_ORDER_FAILED"} <= s.prohibited_codes()
    assert s.recommended_action.code == "CHECK_CURRENT_STATE"
    assert can_capture(a)[0] is False


def test_retry_allowed_only_after_verified_not_captured(db):
    from datetime import timedelta
    from app.timeutil import utcnow
    ref, t = "ORD-TO-2", utcnow() - timedelta(minutes=10)
    kw = dict(order_ref=ref, verified=True, refresh=False)
    service.record_event(db, source="paypal_api", event_type="ORDER_CREATED", transaction_state="CREATED", http_status=201, timestamp=t, amount=10, **kw)
    service.record_event(db, source="merchant_log", event_type="CAPTURE_REQUESTED", timestamp=t + timedelta(seconds=10), **kw)
    service.record_event(db, source="merchant_log", event_type="REQUEST_TIMEOUT", timestamp=t + timedelta(seconds=40), **kw)
    service.record_event(db, source="paypal_api", event_type="STATE_LOOKUP", transaction_state="APPROVED", http_status=200, timestamp=t + timedelta(seconds=60), **kw)
    a = service.analyze_order(db, ref)
    s = evaluate(a)
    assert a.verified_not_captured and s.recommended_action.code == "RETRY_WITH_APPROVAL"
    assert "CAPTURE_AGAIN" in s.prohibited_codes() and "RETRY_PAYMENT" in s.prohibited_codes()  # blind retry still blocked
    assert can_capture(a)[0] is True and s.requires_human_approval


def test_conflicting_evidence_escalates(db):
    ref = "ORD-CF-1"
    kw = dict(order_ref=ref, verified=True, refresh=False, source="paypal_webhook")
    service.record_event(db, event_type="CAPTURE_COMPLETED", transaction_state="COMPLETED", capture_ref="C1", external_event_id="e1", amount=5, **kw)
    service.record_event(db, event_type="CAPTURE_DENIED", transaction_state="DECLINED", capture_ref="C2", external_event_id="e2", **kw)
    a = service.analyze_order(db, ref)
    s = evaluate(a)
    assert a.outcome_certainty == "conflicting" and s.needs_further_investigation and s.recommended_action.code == "ESCALATE_FOR_REVIEW"
    assert s.evidence_confidence == "low"


def test_two_distinct_captures_raise_duplicate_charge_warning(db, client):
    ref = "ORD-DUP-CHG"
    kw = dict(order_ref=ref, verified=True, source="paypal_webhook", event_type="CAPTURE_COMPLETED", transaction_state="COMPLETED")
    service.record_event(db, capture_ref="C1", external_event_id="x1", amount=500, **kw)
    service.record_event(db, capture_ref="C2", external_event_id="x2", **kw)
    s = client.get("/api/dashboard/summary").json()
    assert s["duplicate_payment_warnings"][0]["captures_completed"] == 2
    inc = service.latest_incident(db, ref)
    sev = inc.severity_reasons
    assert any(f["factor"] == "potential_duplicate_charge" and f["points"] == 4 for f in sev["factors"])


# ---------------- capture endpoint gating ----------------
def test_capture_endpoint_requires_explicit_approval_and_is_gated(client, db, monkeypatch):
    inc = seed.run_scenario(db, "timeout_after_success")
    called = []
    monkeypatch.setattr(paypal, "capture_order", lambda *a, **k: called.append(1))
    assert client.post(f"/api/paypal/orders/{inc.order_id}/capture", json={"approved_by": "amna", "confirm": False}).status_code == 400
    r = client.post(f"/api/paypal/orders/{inc.order_id}/capture", json={"approved_by": "amna", "confirm": True})
    assert r.status_code == 409 and r.json()["detail"]["blocked"] and not called  # PayPal was never called
    audit = client.get(f"/api/incidents/{inc.id}/audit").json()
    assert any(x["action"] == "capture_blocked_by_safety_engine" for x in audit)


def test_capture_flow_records_timeout_as_unknown_outcome(client, db, monkeypatch):
    ref = "ORD-LIVE-1"
    service.record_event(db, order_ref=ref, source="paypal_api", event_type="ORDER_CREATED", transaction_state="CREATED", http_status=201, verified=True, amount=20)
    service.get_order(db, ref).capture_request_id = "req-1"
    db.commit()

    def timeout(*a, **k):
        raise paypal.PayPalTimeout("slow")
    monkeypatch.setattr(paypal, "capture_order", timeout)
    r = client.post(f"/api/paypal/orders/{ref}/capture", json={"approved_by": "amna", "confirm": True})
    assert r.status_code == 200 and r.json()["outcome"] == "timeout_unknown"
    # second attempt is now blocked until state is verified
    assert client.post(f"/api/paypal/orders/{ref}/capture", json={"approved_by": "amna", "confirm": True}).status_code == 409
    d = client.get(f"/api/transactions/{ref}").json()
    assert d["incident"]["incident_type"] == "outcome_unknown"


def test_successful_capture_uses_idempotency_key(client, db, monkeypatch):
    ref = "ORD-LIVE-2"
    service.record_event(db, order_ref=ref, source="paypal_api", event_type="ORDER_CREATED", transaction_state="CREATED", http_status=201, verified=True, amount=20)
    service.get_order(db, ref).capture_request_id = "req-xyz"
    db.commit()
    seen = {}

    def ok(order_id, request_id):
        seen["rid"] = request_id
        return paypal.PayPalResponse(201, {"status": "COMPLETED", "purchase_units": [{"payments": {"captures": [{"id": "CAP9", "status": "COMPLETED", "amount": {"value": "20.00", "currency_code": "USD"}}]}}]}, "dbg1", request_id)
    monkeypatch.setattr(paypal, "capture_order", ok)
    r = client.post(f"/api/paypal/orders/{ref}/capture", json={"approved_by": "amna", "confirm": True})
    assert r.json()["outcome"] == "completed" and seen["rid"] == "req-xyz"
    assert client.post(f"/api/paypal/orders/{ref}/capture", json={"approved_by": "amna", "confirm": True}).status_code == 409  # already captured


# ---------------- redaction ----------------
def test_redaction():
    out = redact({"access_token": "abc", "payer": {"email_address": "a@b.com"}, "note": "card 4111 1111 1111 1111 mail x@y.io", "ok": "value"})
    assert out["access_token"] == "[REDACTED]" and out["payer"] == "[REDACTED]" and out["ok"] == "value"
    assert "4111" not in out["note"] and "x@y.io" not in out["note"]
    assert "1730000000000" in redact_text("ts 1730000000000")  # a 13-digit timestamp (fails Luhn) is not mangled
    assert "Bearer [REDACTED_TOKEN]" in redact_text("Authorization: Bearer abc.def-123")


# ---------------- HTTP API ----------------
def test_filters_search_resolve_report_audit(client):
    client.post("/api/demo/seed-all")
    assert client.get("/api/incidents", params={"severity": "high"}).json()["total"] >= 1
    assert client.get("/api/incidents", params={"type": "payment_pending"}).json()["total"] == 1
    assert client.get("/api/incidents", params={"status": "resolved"}).json()["total"] == 0
    inc = client.get("/api/incidents", params={"type": "timeout_after_success"}).json()["items"][0]
    assert client.get(f"/api/incidents/{inc['reference']}").status_code == 200  # reference works as an ID
    assert client.get(f"/api/incidents/{inc['id']}/report").status_code == 409  # not investigated yet
    client.post(f"/api/incidents/{inc['id']}/investigate", json={"use_llm": False})
    md = client.get(f"/api/incidents/{inc['id']}/report", params={"format": "md"})
    assert md.status_code == 200 and "SYNTHETIC TEST DATA" in md.text and "Actions that should NOT be taken" in md.text and "inferred" in md.text
    rep = client.get(f"/api/incidents/{inc['id']}/report").json()
    assert rep["investigated_at"] and rep["safety_warnings"] and rep["recommended_action"]["code"] == "RECONCILE_LOCAL_ORDER"
    res = client.post(f"/api/incidents/{inc['id']}/resolve", json={"resolved_by": "amna", "note": "reconciled"})
    assert res.json()["status"] == "resolved"
    actions = [a["action"] for a in client.get(f"/api/incidents/{inc['id']}/audit").json()]
    assert {"investigation_run", "incident_resolved", "report_exported"} <= set(actions)
    s = client.get("/api/dashboard/summary").json()
    assert s["transactions"]["total"] == 4 and s["incidents"]["resolved"] == 1 and s["transactions"]["synthetic"] == 4
    found = client.get("/api/transactions/search", params={"q": inc["order_id"][:10]}).json()["items"]
    assert found and found[0]["data_origin"] == "synthetic"


def test_open_incident_by_transaction_id(client, db):
    service.record_event(db, order_ref="ORD-HEALTHY", source="paypal_api", event_type="ORDER_CREATED", transaction_state="CREATED", http_status=201, verified=True, amount=9)
    r = client.post("/api/incidents", json={"transaction_id": "ORD-HEALTHY"})
    assert r.status_code == 201 and r.json()["incident_type"] == "unclassified" and r.json()["data_origin"] == "sandbox"
    assert client.post("/api/incidents", json={"transaction_id": "ORD-NOPE"}).status_code == 404


def test_ingest_cannot_forge_paypal_evidence(client):
    inc = client.post("/api/demo/scenarios/B").json()["incident"]
    r = client.post(f"/api/incidents/{inc['id']}/events", json={"source": "paypal_webhook", "event_type": "CAPTURE_COMPLETED", "transaction_state": "COMPLETED"})
    assert r.status_code == 201 and r.json()["event"]["source"] == "merchant_log" and r.json()["event"]["verified"] is False
    tl = client.get(f"/api/incidents/{inc['id']}/timeline").json()
    assert tl["authoritative_state"] == "PENDING"  # forged 'completed' did not change the authoritative state


def test_api_key_and_demo_gate(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "super-secret-key-123")
    assert client.get("/api/incidents").status_code == 401
    assert client.get("/api/incidents", headers={"X-API-Key": "super-secret-key-123"}).status_code == 200
    assert client.get("/api/health").status_code == 200
    monkeypatch.setattr(settings, "enable_demo", False)
    assert client.post("/api/demo/scenarios/A", headers={"X-API-Key": "super-secret-key-123"}).status_code == 403
    monkeypatch.setattr(settings, "enable_demo", True)
    monkeypatch.setattr(settings, "api_key", None)
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.post("/api/demo/scenarios/A").status_code == 403  # production without API_KEY: demo is closed


def test_health_never_leaks_secrets(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-very-secret-1234")
    assert "sk-or-very-secret" not in client.get("/api/health").text


def test_unknown_scenario_404(client):
    assert client.post("/api/demo/scenarios/Z").status_code == 404
