import json

from app import paypal


def webhook(event_id="WH-1", event_type="PAYMENT.CAPTURE.COMPLETED", order="ORD-REAL-1", capture="CAP-REAL-1"):
    return {
        "id": event_id, "event_type": event_type, "create_time": "2026-11-05T10:00:00Z", "summary": "Payment completed",
        "resource": {"id": capture, "status": "COMPLETED", "amount": {"value": "42.00", "currency_code": "USD"},
                     "payer": {"email_address": "buyer@example.com"},
                     "supplementary_data": {"related_ids": {"order_id": order}}},
    }


HEADERS = {"paypal-auth-algo": "SHA256withRSA", "paypal-cert-url": "https://api.sandbox.paypal.com/cert", "paypal-transmission-id": "t1",
           "paypal-transmission-sig": "sig", "paypal-transmission-time": "2026-11-05T10:00:00Z"}


def post(client, body):
    return client.post("/api/webhooks/paypal", content=json.dumps(body), headers={**HEADERS, "content-type": "application/json"})


def test_bad_signature_is_rejected_and_not_stored(client, monkeypatch):
    monkeypatch.setattr(paypal, "verify_webhook_signature", lambda *a, **k: False)
    r = post(client, webhook())
    assert r.status_code == 400
    assert client.get("/api/transactions/search", params={"q": "ORD-REAL-1"}).json()["items"] == []


def test_verified_webhook_stored_then_duplicate_is_idempotent(client, monkeypatch):
    monkeypatch.setattr(paypal, "verify_webhook_signature", lambda *a, **k: True)
    r1 = post(client, webhook())
    assert r1.status_code == 200 and r1.json()["status"] == "processed"
    r2 = post(client, webhook())
    r3 = post(client, webhook())
    assert r2.json()["status"] == "duplicate_ignored" and r3.json()["delivery_count"] == 3
    d = client.get("/api/transactions/ORD-REAL-1").json()
    assert d["order"]["payment_processing_count"] == 1
    assert len([e for e in d["timeline"] if e["event_type"] == "CAPTURE_COMPLETED"]) == 1
    assert d["order"]["data_origin"] == "sandbox"
    assert d["incident"]["incident_type"] == "duplicate_event_delivery"


def test_pii_is_redacted_in_stored_payload(client, monkeypatch, db):
    from app.models import TransactionEvent
    monkeypatch.setattr(paypal, "verify_webhook_signature", lambda *a, **k: True)
    post(client, webhook())
    ev = db.query(TransactionEvent).first()
    assert "buyer@example.com" not in json.dumps(ev.payload_redacted)


def test_verification_outage_fails_safe(client, monkeypatch):
    def boom(*a, **k):
        raise paypal.PayPalTimeout("down")
    monkeypatch.setattr(paypal, "verify_webhook_signature", boom)
    r = post(client, webhook())
    assert r.status_code == 503  # PayPal will retry; nothing untrusted was stored
    assert client.get("/api/transactions/search", params={"q": "ORD-REAL-1"}).json()["items"] == []


def test_missing_webhook_id_refuses_to_trust(client, monkeypatch, settings):
    monkeypatch.setattr(settings, "paypal_webhook_id", None)
    assert post(client, webhook()).status_code == 503


def test_unverified_dev_mode_never_counts_as_evidence(client, monkeypatch, settings):
    monkeypatch.setattr(settings, "paypal_webhook_id", None)
    monkeypatch.setattr(settings, "allow_unverified_webhooks", True)
    r = post(client, webhook(order="ORD-SIM-1"))
    assert r.json()["status"] == "stored_unverified"
    d = client.get("/api/transactions/ORD-SIM-1").json()
    assert d["safety"]["confirmed_state"] == "UNKNOWN" and d["safety"]["evidence_confidence"] == "inconclusive"
    assert d["order"]["status"] == "pending"  # an unverified 'completed' did not mark the order paid


def test_invalid_json_rejected(client):
    assert client.post("/api/webhooks/paypal", content=b"not json", headers=HEADERS).status_code == 400
