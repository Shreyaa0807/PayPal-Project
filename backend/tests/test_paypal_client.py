from app import paypal


def test_create_order_falls_back_to_legacy_body_when_rejected(monkeypatch, settings):
    monkeypatch.setattr(settings, "paypal_client_id", "id-xxxxxxxx")
    monkeypatch.setattr(settings, "paypal_client_secret", "secret-xxxxxxxx")
    seen = []

    def fake_call(method, path, json_body=None, request_id=None):
        seen.append((json_body, request_id))
        if "payment_source" in json_body:
            raise paypal.PayPalError(422, {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INVALID_PARAMETER_VALUE", "field": "/payment_source/paypal/experience_context/return_url", "description": "bad url"}]}, "dbg")
        return paypal.PayPalResponse(201, {"id": "ORD1", "status": "CREATED", "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/x"}]}, "dbg2", request_id)

    monkeypatch.setattr(paypal, "_call", fake_call)
    r = paypal.create_order("10.00", "USD", None)
    assert r.body["id"] == "ORD1" and len(seen) == 2
    assert "application_context" in seen[1][0] and seen[0][1] != seen[1][1]  # fresh idempotency key for the new body
    assert paypal.approve_link(r.body) == "https://sandbox.paypal.com/x"


def test_error_detail_text_shows_field_and_reason():
    e = paypal.PayPalError(422, {"details": [{"issue": "INVALID_PARAMETER_VALUE", "field": "/purchase_units/@reference_id=='default'/amount/value", "description": "The value is invalid"}]})
    assert "INVALID_PARAMETER_VALUE at /purchase_units" in e.detail_text and "The value is invalid" in e.detail_text
