"""AI log reader: turn a pasted, messy merchant log into structured events.

The LLM does the reading (the one job rules can't do well); deterministic code decides what is accepted:
  * every event must quote text that really exists in the pasted log (no hallucinated events),
  * only a small set of event types is allowed, with a parseable timestamp and a known state,
  * extracted events are stored as NON-authoritative merchant-log observations, so a pasted log can never
    change the confirmed payment state, only the timeline and the merchant-side picture.
If the LLM is unavailable, a rules-based extractor runs through the same validator.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .. import constants as C
from .. import service
from ..config import get_settings
from ..models import Incident
from ..redact import redact_text
from ..timeutil import as_utc
from . import llm
from .validator import extract_json

MAX_CHARS = 12000
MAX_EVENTS = 50
ALLOWED_TYPES = {C.ET_CAPTURE_REQUESTED, C.ET_REQUEST_TIMEOUT, C.ET_LOCAL_STATE, C.ET_OTHER}
STATE_WORDS = "PENDING|COMPLETED|PAID|FAILED|DECLINED|CREATED"
KNOWN_ERRORS = "INSTRUMENT_DECLINED|ORDER_NOT_APPROVED|ORDER_ALREADY_CAPTURED|PAYER_ACTION_REQUIRED|TRANSACTION_REFUSED|DUPLICATE_INVOICE_ID"
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")

SYSTEM_PROMPT = f"""You extract payment-related events from a raw merchant application log.
Return ONE JSON object: {{"events":[{{"timestamp":"as written in the log","event_type":"...","transaction_state":"... or null","error_code":"... or null","evidence_quote":"exact text copied from one log line"}}]}}

event_type must be one of:
- CAPTURE_REQUESTED: the app sent/submitted a capture request to PayPal
- REQUEST_TIMEOUT: a request to PayPal timed out / got no response
- LOCAL_STATE_RECORDED: the app recorded or changed its own order status (set transaction_state to one of {STATE_WORDS.replace('|', ', ')})
- OTHER: any other payment-relevant line (an error code, a retry, etc.)

Rules: use ONLY what is written in the log. Copy evidence_quote verbatim from the log. Do not infer, guess or invent events,
timestamps, states or error codes. Skip lines that are not about this payment. Output JSON only."""


# ------------------------------------------------------------------ extraction
def _llm_extract(text: str, model: str) -> list[dict]:
    raw = llm.call_openrouter([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "LOG:\n" + text}], model)
    data = extract_json(raw)
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("no events list")
    return [e for e in events if isinstance(e, dict)]


def _rules_extract(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        m = TS_RE.search(line)
        if not m:
            continue
        msg = line.strip()
        ev: dict | None = None
        if re.search(r"time[d ]?-?out|ETIMEDOUT|ReadTimeout|deadline exceeded", msg, re.I):
            ev = {"event_type": C.ET_REQUEST_TIMEOUT}
        elif (sm := re.search(rf"(?:marked|set|left|status|state)\W+(?:as\W+|to\W+|in\W+)?({STATE_WORDS})\b", msg, re.I)):
            ev = {"event_type": C.ET_LOCAL_STATE, "transaction_state": sm.group(1).upper()}
        elif re.search(r"capture", msg, re.I) and re.search(r"POST|submit|request|initiat|sent", msg, re.I):
            ev = {"event_type": C.ET_CAPTURE_REQUESTED}
        elif (em := re.search(rf"\b({KNOWN_ERRORS})\b", msg)):
            ev = {"event_type": C.ET_OTHER, "error_code": em.group(1)}
        if ev:
            out.append({**ev, "timestamp": m.group(1), "evidence_quote": msg})
    return out


# ------------------------------------------------------------------ validation (the part that keeps the AI honest)
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    m = TS_RE.search(str(s))
    if not m:
        return None
    try:
        dt = datetime.fromisoformat(m.group(1).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # logs without a zone are treated as UTC


def validate_events(raw: list[dict], log_text: str) -> tuple[list[dict], list[dict]]:
    valid: list[dict] = []
    rejected: list[dict] = []
    norm_log = _norm(log_text)
    for e in raw[: MAX_EVENTS * 2]:
        quote = _norm(str(e.get("evidence_quote") or ""))
        etype = str(e.get("event_type") or "").upper()

        def reject(reason: str) -> None:
            rejected.append({"reason": reason, "quote": quote[:160]})

        if etype not in ALLOWED_TYPES:
            reject(f"unsupported event_type '{etype[:30]}'")
        elif len(quote) < 6 or quote not in norm_log:
            reject("quote not found in the pasted log (possible hallucination)")
        else:
            ts = _parse_ts(e.get("timestamp")) or _parse_ts(quote)
            state = (str(e["transaction_state"]).upper() if e.get("transaction_state") else None)
            code = e.get("error_code")
            if ts is None:
                reject("no parseable timestamp")
            elif etype == C.ET_LOCAL_STATE and (state not in C.ALL_STATES or state == "CONFLICT" or state.lower() not in quote.lower()):
                reject("state missing or not present in the quoted line")
            else:
                code = str(code) if code and re.fullmatch(r"[A-Z][A-Z0-9_]{3,60}", str(code)) and str(code).lower() in quote.lower() else None
                valid.append({"event_type": etype, "timestamp": ts, "transaction_state": state if etype == C.ET_LOCAL_STATE else None,
                              "error_code": code, "quote": quote})
        if len(valid) >= MAX_EVENTS:
            break
    return valid, rejected


# ------------------------------------------------------------------ ingestion
def ingest_log(db: Session, incident: Incident, log_text: str, use_llm: bool = True, actor: str = "user") -> dict:
    settings = get_settings()
    text = redact_text(log_text)[:MAX_CHARS]  # nothing sensitive goes to the LLM or into the database
    method = "rules"
    notes: list[str] = []
    valid: list[dict] = []
    rejected: list[dict] = []

    if use_llm and settings.openrouter_api_key:
        for model in settings.llm_models:
            try:
                v, r = validate_events(_llm_extract(text, model), text)
            except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as e:
                notes.append(f"{model}: {str(e)[:80]}")
                continue
            if v:
                valid, rejected, method = v, r, f"llm:{model}"
                break
            notes.append(f"{model}: no valid events")
    if method == "rules":
        valid, rejected = validate_events(_rules_extract(text), text)

    existing = {(e.event_type, as_utc(e.timestamp).replace(microsecond=0), (e.payload_redacted or {}).get("quote"))
                for e in service.order_events(db, incident.order_id)}
    ingested: list[dict] = []
    skipped = 0
    for ev in valid:
        key = (ev["event_type"], as_utc(ev["timestamp"]).replace(microsecond=0), ev["quote"])
        if key in existing:
            skipped += 1
            continue
        service.record_event(db, order_ref=incident.order_id, source=C.SOURCE_LOG, event_type=ev["event_type"], timestamp=ev["timestamp"],
                             transaction_state=ev["transaction_state"], error_code=ev["error_code"], refresh=False,
                             payload={"quote": ev["quote"], "origin": "pasted_log", "extracted_by": method})
        existing.add(key)
        ingested.append({"event_type": ev["event_type"], "timestamp": ev["timestamp"].isoformat(), "transaction_state": ev["transaction_state"],
                         "error_code": ev["error_code"], "quote": ev["quote"]})

    service.refresh_order(db, incident.order_id)
    service.audit(db, actor=actor, action="log_ingested", incident_id=incident.id, order_ref=incident.order_id,
                  details={"method": method, "ingested": len(ingested), "rejected": len(rejected), "skipped_duplicates": skipped, "notes": notes})
    return {"method": method, "ingested": len(ingested), "rejected": rejected, "skipped_duplicates": skipped, "events": ingested,
            "notes": notes, "note": "Extracted events are stored as merchant-log observations. They never change PayPal's confirmed payment state."}
