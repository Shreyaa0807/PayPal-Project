# PayPal PaymentOps AI — Autonomous Payment Failure Investigator

> Move from "payment failed" to **what happened, the evidence for it, and the safest next step.**

PaymentOps AI collects PayPal Sandbox API responses, verified webhooks and merchant logs, reconstructs the transaction timeline, and produces an evidence-grounded root-cause analysis. **AI explains; deterministic rules decide what is safe.** The AI can never recommend a prohibited action (e.g. capturing twice), because every output is validated against a rule engine that runs without the LLM.

Built for the *Build What's Next with PayPal and AI* hackathon. Sandbox funds only.

## Architecture

```
PayPal Sandbox ─┬─ API responses ──┐
                ├─ Webhooks (signature-verified) ─► normalize ─► PostgreSQL (Neon) event store
Merchant logs ──┘                                   (dedupe by event ID)        │
Synthetic fixtures (clearly labelled) ──────────────────────────────────────────┤
                                                                                ▼
                                   timeline reconstruction ─► safety engine ─► severity engine
                                   (confirmed vs inferred)    (deterministic)   (explainable)
                                                                                │
                                              OpenRouter LLM (redacted context) ▼
                                   validate: schema · real evidence IDs · no prohibited action
                                                     · no invented identifiers  ──► report
                                   (any failure ─► rules-only fallback, fails safe)
```

| Part | Tech |
|---|---|
| Frontend | Next.js + TypeScript (separate folder) |
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2 |
| Database | PostgreSQL on Neon |
| LLM | OpenRouter (free models), model list configurable |
| Payments | PayPal Sandbox: Orders v2 API, webhooks + signature verification |

### What makes it safe
- **Safety engine (`app/safety.py`)** — rules R1–R9: confirmed completion blocks re-capture; pending ⇒ check state first; timeout ⇒ outcome *unknown*, never "failed"; conflicting evidence ⇒ escalate; all consequential actions need human approval; retries only via PayPal-Request-Id idempotency after state is verified.
- **Confidence is evidence-based** — final confidence = min(rule-derived evidence quality, LLM's self-report).
- **Authoritative evidence only from PayPal** — a successful API response or a *signature-verified* webhook. Merchant logs and events posted to the ingest endpoint are never authoritative.
- **Redaction** before storage, export and LLM calls (tokens, secrets, emails, card numbers, DB URLs).
- **Audit log** for investigations, approvals, blocked actions, duplicate deliveries, exports.

## Run it locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # then fill in the values
uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000/docs (interactive API docs). Tables are created automatically on startup (`backend/schema.sql` has the DDL if you prefer the Neon SQL editor).

**Seed the demo and investigate (no keys needed besides the DB):**
```bash
curl -X POST "localhost:8000/api/demo/scenarios/A?investigate=true"
```

### Environment variables
See `backend/.env.example`. Required: `DATABASE_URL` (Neon), `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`, `OPENROUTER_API_KEY`. Secrets live only in server-side env vars and are never returned by any endpoint.

### PayPal Sandbox setup
1. developer.paypal.com → **Apps & Credentials** → *Sandbox* → **Create App** (Merchant). Copy Client ID + Secret.
2. **Testing Tools → Sandbox Accounts**: use the Personal account to approve test payments.
3. After deploying (needs a public HTTPS URL): on the app page, **Add Webhook** → URL `https://<your-backend>/api/webhooks/paypal`, select `CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.PENDING`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REFUNDED`. Copy the **Webhook ID** into `PAYPAL_WEBHOOK_ID`.
4. Note: events from the Webhooks *Simulator* can't be verified the normal way. Test signature verification with a real sandbox payment.

### Real sandbox flow
```text
POST /api/paypal/orders {"amount":"10.00"}      → returns approve_url
(buyer approves with the sandbox Personal account)
POST /api/paypal/orders/{id}/capture {"approved_by":"you","confirm":true}   ← human-approved, safety-gated
GET  /api/paypal/orders/{id}                    → authoritative state, recorded as evidence
```
PayPal's webhook then arrives at `/api/webhooks/paypal`; anomalies automatically open an incident.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | status (no secrets) |
| GET | `/api/dashboard/summary` | counts, severity breakdown, duplicate warnings, recent activity |
| GET | `/api/incidents` | list + filters: `status,type,severity,date_from,date_to,q,synthetic,limit,offset` |
| POST | `/api/incidents` | open by transaction ID (order/capture ID or incident ref) |
| GET | `/api/incidents/{id}` | details + latest investigation + safety result (`id` or `INC-XXXXXX`) |
| POST | `/api/incidents/{id}/investigate` | run investigation `{use_llm, actor}` |
| GET | `/api/incidents/{id}/timeline` | normalized events, findings, confirmed vs inferred |
| POST | `/api/incidents/{id}/events` | ingest diagnostic event (stored as non-authoritative merchant log) |
| POST | `/api/incidents/{id}/resolve` | mark resolved |
| GET | `/api/incidents/{id}/report?format=json\|md` | export report |
| GET | `/api/incidents/{id}/audit` | audit trail |
| GET | `/api/transactions/search?q=` · `/api/transactions/{ref}` | transaction explorer |
| POST | `/api/paypal/orders` · GET `/api/paypal/orders/{id}` · POST `/api/paypal/orders/{id}/capture` | sandbox lifecycle |
| POST | `/api/webhooks/paypal` | verified webhook receiver |
| GET/POST/DELETE | `/api/demo/scenarios`, `/api/demo/scenarios/{A-D}`, `/api/demo/seed-all`, `/api/demo/reset` | Demo Lab (synthetic only) |

Import `backend/postman_collection.json` into Postman for ready-made requests.

Every incident/transaction carries `data_origin: "synthetic" | "sandbox"` so the UI can label seeded vs. real evidence.

## Demo scenarios (all labelled synthetic)
- **A – Timeout after successful capture** → reconcile local order; capture-again blocked.
- **B – Payment pending** → check authoritative state; neither success nor failure claimed.
- **C – Duplicate webhook delivery** → same event ID delivered 3×, processed once; duplicates ≠ duplicate charges.
- **D – Merchant record disagrees with PayPal** → reconcile; high severity because of amount and retry risk.

Events pass through the real ingestion path, so deduplication and incident detection are exercised, not faked.

## Tests & evaluation
```bash
cd backend
python -m pytest -q            # 44 tests; always uses an in-memory SQLite DB, never your Neon database
python -m scripts.run_eval     # accuracy / evidence-grounding / unsafe-recommendation metrics
```
Four scripted scenarios are a smoke test, **not proof of production reliability**.

## Deploy on Render
`render.yaml` is included (free web service). Set the env vars in the Render dashboard. Free instances sleep when idle, so open `/api/health` before a demo; PayPal retries webhooks that arrive while it is waking.

## Frontend integration
Point the Next.js app at the backend (`NEXT_PUBLIC_API_URL`). If you set `API_KEY`, call the API from Next.js server-side route handlers and add the `X-API-Key` header there so the key never reaches the browser.

## Tools used (for the submission)
- **PayPal**: Sandbox Orders v2 API, webhooks and webhook signature verification API.
- **AI**: OpenRouter-hosted LLM for structured root-cause analysis (JSON, schema-validated), with a deterministic rules-only fallback.
- **Neon** (PostgreSQL), **FastAPI**, **Render** (hosting).

## Limitations
- Sandbox only; no real money. No autonomous captures, retries or refunds. Capture is human-approved and safety-gated.
- Diagnosis covers the incident types above; it says "inconclusive" instead of guessing.
- Bank-side causes are never asserted without evidence.

## License
MIT — see `LICENSE`.
