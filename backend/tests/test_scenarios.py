"""The three required MVP scenarios (+ the optional fourth), end to end through the HTTP API, rules-only (no LLM)."""
import pytest

PROHIBITED_WHEN_COMPLETED = {"CAPTURE_AGAIN", "RETRY_PAYMENT", "CREATE_NEW_ORDER"}


def run(client, scenario):
    r = client.post(f"/api/demo/scenarios/{scenario}?investigate=true&use_llm=false")
    assert r.status_code == 201, r.text
    return r.json()


def test_scenario_a_timeout_after_success(client):
    out = run(client, "A")
    inc, inv, safety = out["incident"], out["investigation"], out["safety"]
    assert out["data_origin"] == "synthetic" and inc["data_origin"] == "synthetic"
    assert inc["incident_type"] == "timeout_after_success"
    assert safety["confirmed_state"] == "COMPLETED"
    assert inv["recommended_action"]["code"] == "RECONCILE_LOCAL_ORDER"
    blocked = {p["code"] for p in inv["prohibited_actions"]}
    assert PROHIBITED_WHEN_COMPLETED <= blocked
    assert inv["confidence"] == "high"
    # Timeline: confirmed vs inferred are distinguished, delayed notification is highlighted
    tl = client.get(f"/api/incidents/{inc['id']}/timeline").json()
    kinds = {e["kind"] for e in tl["entries"]}
    assert {"confirmed", "inferred"} <= kinds
    assert {f["code"] for f in tl["findings"]} >= {"RESPONSE_LOST", "DELAYED_NOTIFICATION", "STATE_MISMATCH"}


def test_scenario_b_pending_is_neither_success_nor_failure(client):
    out = run(client, "B")
    inc, inv, safety = out["incident"], out["investigation"], out["safety"]
    assert inc["incident_type"] == "payment_pending"
    assert safety["confirmed_state"] == "PENDING"
    assert inv["recommended_action"]["code"] == "CHECK_CURRENT_STATE"
    blocked = {p["code"] for p in inv["prohibited_actions"]}
    assert {"CAPTURE_AGAIN", "RETRY_PAYMENT", "MARK_ORDER_FAILED"} <= blocked
    assert inv["confidence"] == "medium"
    assert inv["missing_information"], "must state what is missing"
    text = (inv["summary"] + inv["root_cause"]).lower()
    assert "failed" not in text.replace("no evidence that the payment failed", "")


def test_scenario_c_duplicate_delivery_processed_once(client):
    out = run(client, "C")
    inc = out["incident"]
    assert inc["incident_type"] == "duplicate_event_delivery"
    detail = client.get(f"/api/incidents/{inc['id']}").json()
    assert detail["order"]["payment_processing_count"] == 1  # processed ONCE despite 3 deliveries
    tl = client.get(f"/api/incidents/{inc['id']}/timeline").json()
    dup = [e for e in tl["entries"] if e["delivery_count"] > 1]
    assert len(dup) == 1 and dup[0]["delivery_count"] == 3
    assert "do not by themselves prove duplicate charges" in " ".join(f["detail"] for f in tl["findings"])
    assert tl["distinct_completed_captures"] == 1
    assert inc["severity"] == "Low"  # duplicate notifications alone are not a high-severity event
    summary = client.get("/api/dashboard/summary").json()
    assert summary["duplicate_payment_warnings"] == []  # no duplicate CHARGE warning


def test_scenario_d_state_mismatch(client):
    out = run(client, "D")
    assert out["incident"]["incident_type"] == "state_mismatch"
    assert out["investigation"]["recommended_action"]["code"] == "RECONCILE_LOCAL_ORDER"
    assert out["incident"]["severity"] in {"High", "Critical"}  # large amount + retry risk


@pytest.mark.parametrize("scenario", ["A", "B", "C", "D"])
def test_no_unsafe_recommendation_and_evidence_is_grounded(client, scenario):
    out = run(client, scenario)
    inv, safety = out["investigation"], out["safety"]
    prohibited = {p["code"] for p in safety["prohibited_actions"]}
    assert inv["recommended_action"]["code"] not in prohibited
    tl = client.get(f"/api/incidents/{out['incident']['id']}/timeline").json()
    ids = {e["id"] for e in tl["entries"]}
    for ev in inv["supporting_evidence"] + inv["contradictory_evidence"]:
        assert ev["event_id"] in ids  # every cited item is real evidence


def test_all_incidents_labelled_synthetic_and_severity_explained(client):
    client.post("/api/demo/seed-all")
    items = client.get("/api/incidents").json()["items"]
    assert len(items) == 4
    for i in items:
        assert i["data_origin"] == "synthetic"
        assert i["severity"] in {"Low", "Medium", "High", "Critical"}
        assert i["severity_reasons"]["reasons"] and "not a fraud" in i["severity_reasons"]["note"]


def test_reset_deletes_only_synthetic(client):
    client.post("/api/demo/seed-all")
    assert client.delete("/api/demo/reset").json()["deleted_synthetic_incidents"] == 4
    assert client.get("/api/incidents").json()["total"] == 0
