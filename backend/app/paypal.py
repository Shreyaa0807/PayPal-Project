"""Thin PayPal REST client. Sandbox only: refuses to run against anything else (hackathon rule)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from uuid import uuid4

import httpx

from .config import get_settings

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"


class PayPalNotConfigured(Exception):
    pass


class PayPalTimeout(Exception):
    """The request to PayPal timed out: the outcome of the call is UNKNOWN."""


class PayPalError(Exception):
    def __init__(self, status: int, body: dict, debug_id: str | None = None):
        self.status, self.body, self.debug_id = status, body, debug_id
        super().__init__(f"PayPal HTTP {status}: {body.get('name') or body.get('error') or ''}")

    @property
    def error_code(self) -> str | None:
        details = self.body.get("details") or []
        if details and details[0].get("issue"):
            return details[0]["issue"]
        return self.body.get("name") or self.body.get("error")

    @property
    def detail_text(self) -> str:
        """Human-readable reason straight from PayPal (issue, field and description)."""
        details = self.body.get("details") or []
        d = details[0] if details else {}
        parts = [self.error_code or f"HTTP {self.status}"]
        if d.get("field"):
            parts.append(f"at {d['field']}")
        text = d.get("description") or self.body.get("message")
        return " ".join(parts) + (f": {text}" if text else "")


@dataclass
class PayPalResponse:
    status: int
    body: dict
    debug_id: str | None
    request_id: str | None = None
    headers: dict = field(default_factory=dict)


_token: dict = {"value": None, "exp": 0.0}


def _check_config() -> None:
    s = get_settings()
    if not s.paypal_configured:
        raise PayPalNotConfigured("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set.")
    if s.paypal_env != "sandbox":
        raise PayPalNotConfigured("Only the PayPal Sandbox is supported (PAYPAL_ENV must be 'sandbox').")


def _client(read_timeout: float | None = None) -> httpx.Client:
    t = get_settings().paypal_timeout_s
    # A short READ timeout still lets the request be sent in full; only the wait for the response is cut short.
    return httpx.Client(base_url=SANDBOX_BASE, timeout=httpx.Timeout(t, read=read_timeout) if read_timeout is not None else t)


def get_access_token() -> str:
    _check_config()
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    s = get_settings()
    try:
        with _client() as c:
            r = c.post("/v1/oauth2/token", data={"grant_type": "client_credentials"}, auth=(s.paypal_client_id, s.paypal_client_secret))
    except httpx.TimeoutException as e:
        raise PayPalTimeout("Timed out while authenticating with PayPal") from e
    if r.status_code != 200:
        raise PayPalError(r.status_code, _json(r), r.headers.get("paypal-debug-id"))
    data = r.json()
    _token["value"], _token["exp"] = data["access_token"], time.time() + int(data.get("expires_in", 300))
    return _token["value"]


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}


def _call(method: str, path: str, *, json_body: dict | None = None, request_id: str | None = None, read_timeout: float | None = None) -> PayPalResponse:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if request_id:
        headers["PayPal-Request-Id"] = request_id  # idempotency key
    try:
        with _client(read_timeout) as c:
            r = c.request(method, path, headers=headers, json=json_body)
    except httpx.TimeoutException as e:
        raise PayPalTimeout(f"{method} {path} timed out") from e
    resp = PayPalResponse(status=r.status_code, body=_json(r), debug_id=r.headers.get("paypal-debug-id"), request_id=request_id)
    if r.status_code >= 400:
        raise PayPalError(r.status_code, resp.body, resp.debug_id)
    return resp


def _order_body(amount: str, currency: str, description: str | None, style: str) -> dict:
    s = get_settings()
    unit = {"reference_id": "default", "description": (description or "PaymentOps AI sandbox order")[:127],
            "amount": {"currency_code": currency, "value": amount}}
    ctx = {"brand_name": "PaymentOps AI", "user_action": "PAY_NOW",
           "return_url": f"{s.frontend_url}/checkout/return", "cancel_url": f"{s.frontend_url}/checkout/cancel"}
    if style == "payment_source":
        return {"intent": "CAPTURE", "purchase_units": [unit], "payment_source": {"paypal": {"experience_context": {
            **ctx, "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED"}}}}
    return {"intent": "CAPTURE", "purchase_units": [unit], "application_context": ctx}  # legacy style, widely accepted


def create_order(amount: str, currency: str, description: str | None, request_id: str | None = None) -> PayPalResponse:
    """Create an order. If PayPal rejects the newer payment_source body, retry once with the legacy application_context body."""
    request_id = request_id or str(uuid4())
    try:
        return _call("POST", "/v2/checkout/orders", json_body=_order_body(amount, currency, description, "payment_source"), request_id=request_id)
    except PayPalError as e:
        if e.status not in (400, 422):
            raise
    return _call("POST", "/v2/checkout/orders", json_body=_order_body(amount, currency, description, "application_context"), request_id=str(uuid4()))


def get_order(order_id: str) -> PayPalResponse:
    return _call("GET", f"/v2/checkout/orders/{order_id}")


def capture_order(order_id: str, request_id: str, read_timeout: float | None = None) -> PayPalResponse:
    return _call("POST", f"/v2/checkout/orders/{order_id}/capture", json_body={}, request_id=request_id, read_timeout=read_timeout)


def approve_link(order_body: dict) -> str | None:
    for link in order_body.get("links") or []:
        if link.get("rel") in ("payer-action", "approve"):
            return link.get("href")
    return None


def verify_webhook_signature(headers: dict, raw_body: bytes, webhook_id: str) -> bool:
    """Ask PayPal to verify the webhook. The raw body is embedded verbatim so the signature check sees the exact bytes."""
    required = ["paypal-auth-algo", "paypal-cert-url", "paypal-transmission-id", "paypal-transmission-sig", "paypal-transmission-time"]
    if any(h not in headers for h in required):
        return False
    token = get_access_token()
    head = json.dumps({
        "auth_algo": headers["paypal-auth-algo"], "cert_url": headers["paypal-cert-url"],
        "transmission_id": headers["paypal-transmission-id"], "transmission_sig": headers["paypal-transmission-sig"],
        "transmission_time": headers["paypal-transmission-time"], "webhook_id": webhook_id,
    })
    payload = head[:-1] + ', "webhook_event": ' + raw_body.decode("utf-8") + "}"
    try:
        with _client() as c:
            r = c.post("/v1/notifications/verify-webhook-signature", content=payload.encode("utf-8"),
                       headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    except httpx.TimeoutException as e:
        raise PayPalTimeout("Webhook verification timed out") from e
    if r.status_code != 200:
        raise PayPalError(r.status_code, _json(r), r.headers.get("paypal-debug-id"))
    return _json(r).get("verification_status") == "SUCCESS"
