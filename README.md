# PaymentOps AI — Frontend

A PayPal-inspired, responsive React + TypeScript + GSAP frontend for the PaymentOps AI hackathon PRD.

## Run

```bash
npm install
npm run dev
```

Open http://localhost:5173.

## Included screens

- Overview: operational KPI cards, illustrative payment-health chart, priority queue, recent incidents.
- Investigations: searchable incident selector, metadata, chronological event timeline, evidence-backed demo diagnosis, safety guidance, local resolution control, downloadable text report.
- Transactions: searchable demo transaction explorer.
- Demo Lab: four clickable seeded failure scenarios.
- Settings: integration status and configuration guidance.

## Important: demo vs live

**This is a frontend-only prototype.** All incident records, diagnoses, confidence percentages, and chart values are synthetic. The app does not connect to PayPal, does not use a live AI service, and does not execute financial actions. Local state changes reset on refresh.

## Backend integration

The PRD proposes Node.js/Express and a Python/FastAPI investigation service. When implementing the backend, replace the fixture source in `src/data.ts` with authenticated calls to:

- `GET /api/incidents`
- `GET /api/incidents/:id`
- `POST /api/incidents/:id/investigate`
- `GET /api/incidents/:id/timeline`
- `POST /api/demo/scenarios/:scenario`

The backend should verify PayPal webhook signatures, deduplicate event IDs, redact sensitive payloads, validate AI outputs, and enforce deterministic payment safety rules. Never place PayPal API secrets in `VITE_` variables or client code. Real PayPal API calls must run on the server.

## UI branding

Uses a PayPal-inspired blue/navy color palette and an original dashboard layout. This is an independent hackathon prototype, not an official PayPal application.
