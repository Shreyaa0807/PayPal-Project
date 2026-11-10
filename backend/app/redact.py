"""Redaction applied before anything is stored, exported, or sent to the LLM (PRD section 12)."""
import re
from typing import Any

from .config import get_settings

SENSITIVE_KEY = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|card|cvv|cvc|iban|account[_-]?number|routing|"
    r"ssn|email|phone|address|given_name|surname|full_name|payer|postal|signature|transmission_sig|cert|"
    r"credential|bearer)",
    re.I,
)
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.I)
PAYPAL_TOKEN = re.compile(r"\bA21[A-Za-z0-9_\-]{20,}\b")
LONG_NUMBER = re.compile(r"\b(?:\d[ \-]?){13,19}\b")
DB_URL = re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s\"']+", re.I)


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def redact_text(text: str) -> str:
    for secret in get_settings().secret_values:
        text = text.replace(secret, "[REDACTED_SECRET]")
    text = DB_URL.sub("[REDACTED_DB_URL]", text)
    text = BEARER.sub("Bearer [REDACTED_TOKEN]", text)
    text = PAYPAL_TOKEN.sub("[REDACTED_TOKEN]", text)
    text = EMAIL.sub("[REDACTED_EMAIL]", text)

    def _num(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[REDACTED_NUMBER]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)

    return LONG_NUMBER.sub(_num, text)


def redact(value: Any) -> Any:
    """Deep-redact dicts/lists/strings. Returns a new structure."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and SENSITIVE_KEY.search(k):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
