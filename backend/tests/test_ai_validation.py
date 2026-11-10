"""The AI output must be validated: real evidence, no prohibited actions, no invented identifiers."""
import json

import pytest

from app import seed, service
from app.ai import llm
from app.ai.validator import violates_by_wording
from app.safety import evaluate


@pytest.fixture
def incident(db, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-1234567890")
    monkeypatch.setattr(settings, "openrouter_models", "model/a,model/b")
    return seed.run_scenario(db, "timeout_after_success")


def evidence_ids(db, inc):
    a = service.analyze_order(db, inc.order_id)
    return {e.event_type: e.id for e in a.entries if e.kind != "inferred"}


def good(ids, **over):
    d = {
        "summary": "Capture timed out but completed.", "root_cause": "The merchant missed the success response.",
        "supporting_evidence": [{"event_id": ids["REQUEST_TIMEOUT"], "statement": "Timeout recorded."},
                                {"event_id": ids["CAPTURE_COMPLETED"], "statement": "Completion confirmed."}],
        "contradictory_evidence": [], "alternative_explanations": ["Webhook handler failed."], "confidence": "high",
        "recommended_action": {"code": "RECONCILE_LOCAL_ORDER", "explanation": "Reconcile the local order. Do not capture again."},
        "prohibited_actions": [{"code": "CAPTURE_AGAIN", "reason": "Already completed."}], "missing_information": [],
    }
    d.update(over)
    return json.dumps(d)


def investigate(db, inc):
    inv, *_ = service.run_investigation(db, inc, use_llm=True)
    return inv


def test_valid_llm_output_is_used(db, incident, monkeypatch):
    ids = evidence_ids(db, incident)
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: good(ids))
    inv = investigate(db, incident)
    assert inv.generated_by == "llm:model/a" and inv.validation["passed"]
    assert inv.llm_confidence == "high" and inv.confidence == "high"


def test_json_in_code_fences_is_parsed(db, incident, monkeypatch):
    ids = evidence_ids(db, incident)
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: "```json\n" + good(ids) + "\n```")
    assert investigate(db, incident).generated_by.startswith("llm:")


def test_prohibited_action_code_is_rejected_and_next_model_tried(db, incident, monkeypatch):
    ids = evidence_ids(db, incident)
    bad = good(ids, recommended_action={"code": "CAPTURE_AGAIN", "explanation": "Capture again."})
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: bad if model == "model/a" else good(ids))
    inv = investigate(db, incident)
    assert inv.generated_by == "llm:model/b"
    assert inv.validation["attempts"][0]["result"] == "rejected"
    assert any("prohibited" in e for e in inv.validation["attempts"][0]["errors"])


def test_prohibited_wording_in_explanation_is_rejected(db, incident, monkeypatch):
    ids = evidence_ids(db, incident)
    bad = good(ids, recommended_action={"code": "RECONCILE_LOCAL_ORDER", "explanation": "Reconcile, then initiate a new capture to be safe."})
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: bad)
    inv = investigate(db, incident)
    assert inv.generated_by == "rules_fallback" and inv.validation["fallback_used"]


def test_invented_evidence_id_is_rejected(db, incident, monkeypatch):
    ids = evidence_ids(db, incident)
    bad = good(ids, supporting_evidence=[{"event_id": "made-up-id", "statement": "Fake."}])
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: bad)
    assert investigate(db, incident).generated_by == "rules_fallback"


def test_invented_error_code_is_rejected(db, incident, monkeypatch):
    ids = evidence_ids(db, incident)
    bad = good(ids, root_cause="PayPal returned INSUFFICIENT_FUNDS_ERROR for this capture.")
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: bad)
    inv = investigate(db, incident)
    assert inv.generated_by == "rules_fallback"
    assert any("ungrounded" in e for a in inv.validation["attempts"] for e in a.get("errors", []))


def test_llm_outage_falls_back_safely(db, incident, monkeypatch):
    def down(m, model):
        raise llm.LLMUnavailable("down")
    monkeypatch.setattr(llm, "call_openrouter", down)
    inv = investigate(db, incident)
    assert inv.generated_by == "rules_fallback"
    assert inv.recommended_action["code"] == "RECONCILE_LOCAL_ORDER"


def test_malformed_json_falls_back(db, incident, monkeypatch):
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: "I think the payment succeeded!")
    assert investigate(db, incident).generated_by == "rules_fallback"


def test_overconfident_llm_is_capped_by_evidence_quality(db, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-1234567890")
    monkeypatch.setattr(settings, "openrouter_models", "model/a")
    inc = seed.run_scenario(db, "payment_pending")
    a = service.analyze_order(db, inc.order_id)
    pend = next(e.id for e in a.entries if e.transaction_state == "PENDING" and e.authoritative)
    out = {"summary": "Pending.", "root_cause": "Payment is pending; final outcome unknown.", "supporting_evidence": [{"event_id": pend, "statement": "PENDING."}],
           "confidence": "high", "recommended_action": {"code": "CHECK_CURRENT_STATE", "explanation": "Check state."}}
    monkeypatch.setattr(llm, "call_openrouter", lambda m, model: json.dumps(out))
    inv = investigate(db, inc)
    assert inv.llm_confidence == "high" and inv.confidence == "medium"  # capped by evidence quality


def test_no_evidence_forces_inconclusive(db, settings):
    inc = service.open_incident(db, service.get_or_create_order(db, "ORD-EMPTY").order_ref)
    inv = investigate(db, inc) if False else service.run_investigation(db, inc, use_llm=False)[0]
    assert inv.confidence == "inconclusive" and "inconclusive" in inv.root_cause.lower() and inv.missing_information


def test_negated_wording_is_allowed():
    assert not violates_by_wording("Do not capture again; reconcile instead.", "CAPTURE_AGAIN")
    assert violates_by_wording("Please capture again.", "CAPTURE_AGAIN")
    assert not violates_by_wording("Never retry the payment blindly.", "RETRY_PAYMENT")


def test_context_sent_to_llm_is_redacted(db, incident, settings, monkeypatch):
    seen = {}
    ids = evidence_ids(db, incident)

    def spy(messages, model):
        seen["text"] = json.dumps(messages)
        return good(ids)
    monkeypatch.setattr(llm, "call_openrouter", spy)
    service.record_event(db, order_ref=incident.order_id, source="merchant_log", event_type="OTHER",
                         payload={"message": "contact buyer@example.com token Bearer abc.def.ghi", "access_token": "A21AAAAAAAAAAAAAAAAAAAAAAAAAA"})
    investigate(db, incident)
    assert "buyer@example.com" not in seen["text"] and "A21AAAA" not in seen["text"]
    assert settings.openrouter_api_key not in seen["text"]
