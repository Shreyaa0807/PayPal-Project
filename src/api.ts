// Backend client + adapters: backend JSON -> the Incident/Event shapes the UI already uses.
import type { Event, Incident, Severity, State } from './data';

const env = (import.meta as any).env ?? {};
export const BASE: string = ((env.VITE_API_BASE_URL as string | undefined) || 'http://localhost:8000').replace(/\/$/, '');
const API_KEY = env.VITE_API_KEY as string | undefined; // only if you set API_KEY on the backend (note: visible in the browser)
const ACTOR = 'PaymentOps Team';

async function req<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (API_KEY) headers['X-API-Key'] = API_KEY;
  let res: Response;
  try {
    res = await fetch(BASE + path, { method: opts.method || 'GET', headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  } catch {
    throw new Error(`cannot reach backend at ${BASE}`);
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      const d = j.detail;
      msg = typeof d === 'string' ? d : d?.reason || d?.error || (d ? JSON.stringify(d) : msg);
    } catch { /* keep default */ }
    throw new Error(msg);
  }
  const ct = res.headers.get('content-type') || '';
  return (ct.includes('json') ? res.json() : res.text()) as Promise<T>;
}

// ---------- helpers ----------
const parseTs = (s: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z'); // naive timestamps are UTC
const p2 = (n: number) => String(n).padStart(2, '0');
const fmtDate = (s: string) => { const d = parseTs(s); return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`; };
const fmtTime = (s: string) => { const d = parseTs(s); return `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`; };
const ago = (s: string) => {
  const sec = Math.max(0, (Date.now() - parseTs(s).getTime()) / 1000);
  if (sec < 60) return 'Just now';
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} hr ago`;
  return `${Math.floor(sec / 86400)} d ago`;
};
const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

const TYPE_LABELS: Record<string, string> = {
  timeout_after_success: 'Capture timeout', payment_pending: 'Pending payment', duplicate_event_delivery: 'Duplicate webhook',
  state_mismatch: 'State mismatch', outcome_unknown: 'Unknown outcome', conflicting_evidence: 'Conflicting evidence',
  potential_duplicate_charge: 'Possible duplicate charge', api_error: 'API error', unclassified: 'Under review',
};
const SOURCE_LABELS: Record<string, string> = { paypal_api: 'PayPal API', paypal_webhook: 'Webhook', merchant_log: 'Merchant log', inference: 'Inference' };
const DO_NOT: Record<string, string> = {
  CAPTURE_AGAIN: 'Do not capture this order again', RETRY_PAYMENT: 'Do not blindly retry the payment',
  CREATE_NEW_ORDER: 'Do not create a replacement order', REFUND: 'Do not refund without explicit human approval',
  MARK_ORDER_FAILED: 'Do not mark the order as failed',
};
const CONF_PCT: Record<string, number> = { high: 92, medium: 62, low: 32, inconclusive: 10 };
const FLAG_TEXT: Record<string, string> = {
  DUPLICATE_DELIVERY: 'duplicate delivery', DELAYED_NOTIFICATION: 'arrived after timeout', STATE_MISMATCH: 'state mismatch',
  UNVERIFIED_EVENT: 'signature not verified', API_ERROR: 'API error', CONFLICTING_EVIDENCE: 'conflicting evidence',
};

const payment = (s: string | null): Incident['payment'] => {
  const x = (s || '').toUpperCase();
  if (x === 'COMPLETED' || x === 'PAID') return 'Completed';
  if (['PENDING', 'CREATED', 'APPROVED', 'PROCESSING'].includes(x)) return 'Pending';
  if (['DECLINED', 'DENIED', 'FAILED'].includes(x)) return 'Failed';
  return 'Unknown';
};

// ---------- adapters ----------
function mapBase(i: any): Partial<Incident> {
  return {
    id: i.reference, order: i.order_id, capture: i.capture_id || '—', type: TYPE_LABELS[i.incident_type] || i.incident_type,
    merchant: 'Northstar Commerce', amount: i.amount ?? 0, currency: i.currency || 'USD', severity: i.severity as Severity,
    status: cap(i.status) as State, payment: payment(i.authoritative_state), created: fmtDate(i.created_at), updated: ago(i.updated_at),
    scenario: i.scenario || i.incident_type, source: i.is_synthetic ? 'Synthetic fixture' : 'PayPal Sandbox',
  };
}

function mapEvent(e: any): Event {
  const inferred = e.kind === 'inferred';
  const flags: string[] = e.flags || [];
  let kind: Event['kind'] = 'info';
  if (inferred) kind = 'info';
  else if (['REQUEST_TIMEOUT', 'API_ERROR', 'CAPTURE_DENIED'].includes(e.event_type)) kind = 'error';
  else if (flags.length || (e.transaction_state || '').toUpperCase() === 'PENDING') kind = 'warning';
  else if (e.authoritative) kind = 'success';
  const bits: string[] = [];
  if (inferred && e.detail) bits.push(e.detail);
  if (e.transaction_state) bits.push(`State ${e.transaction_state}`);
  if (e.http_status) bits.push(`HTTP ${e.http_status}`);
  if (e.error_code) bits.push(e.error_code);
  if (e.delivery_count > 1) bits.push(`delivered ${e.delivery_count}× (processed once)`);
  flags.filter((f) => FLAG_TEXT[f] && f !== 'DUPLICATE_DELIVERY' && f !== 'API_ERROR' && !(f === 'DELAYED_NOTIFICATION' && e.event_type === 'REQUEST_TIMEOUT')).forEach((f) => bits.push(FLAG_TEXT[f]));
  if (e.is_synthetic && !inferred) bits.push('synthetic');
  return {
    id: inferred ? 'INFERRED' : `EVT-${String(e.id).slice(0, 6).toUpperCase()}`, time: fmtTime(e.timestamp),
    title: String(e.label).replace(/\s*\((webhook|API response|merchant log)\)$/, ''), detail: bits.join(' · ') || e.label,
    source: SOURCE_LABELS[e.source] || e.source, kind, fact: !inferred,
  };
}

export async function getDetail(ref: string): Promise<Partial<Incident>> {
  const [d, tl] = await Promise.all([req(`/api/incidents/${ref}`), req(`/api/incidents/${ref}/timeline`)]);
  const shortId = new Map<string, string>(tl.entries.map((e: any) => [e.id, e.kind === 'inferred' ? 'INFERRED' : `EVT-${String(e.id).slice(0, 6).toUpperCase()}`]));
  const ev = (list: any[]) => (list || []).map((x) => `${shortId.get(x.event_id) || x.event_id}: ${x.statement}`);
  const inv = d.investigation;
  const safety = d.safety;
  const base = mapBase(d.incident);
  const events: Event[] = tl.entries.map(mapEvent);
  if (!inv) {
    return {
      ...base, events, summary: `${d.incident.title}. Run an investigation to generate the AI analysis.`, rootCause: 'Not investigated yet.',
      recommendation: safety.recommended_action.explanation, prohibited: safety.prohibited_actions.map((p: any) => `${DO_NOT[p.code] || p.code} — ${p.reason}`),
      evidence: [], contradictions: [], alternatives: [], missing: [], confidence: CONF_PCT[safety.evidence_confidence] ?? 10,
      confidenceLabel: cap(safety.evidence_confidence), generatedBy: 'Not run yet', agentSteps: [],
    };
  }
  return {
    ...base, events, summary: inv.summary, rootCause: inv.root_cause, recommendation: `${inv.recommended_action.explanation}`,
    prohibited: inv.prohibited_actions.map((p: any) => `${DO_NOT[p.code] || p.code} — ${p.reason}`),
    evidence: ev(inv.supporting_evidence), contradictions: ev(inv.contradictory_evidence), alternatives: inv.alternative_explanations || [],
    missing: inv.missing_information || [], confidence: CONF_PCT[inv.confidence] ?? 10, confidenceLabel: cap(inv.confidence),
    generatedBy: inv.generated_by === 'rules_fallback' ? 'Rules engine (AI unavailable)' : inv.generated_by.replace('llm:', 'AI · '),
    agentSteps: inv.validation?.agent_steps || [],
  };
}

// ---------- API calls ----------
export const health = () => req('/api/health');
export async function listIncidents(): Promise<Partial<Incident>[]> {
  const r = await req('/api/incidents?limit=200');
  return r.items.map(mapBase);
}
export async function investigate(ref: string) {
  await req(`/api/incidents/${ref}/investigate`, { method: 'POST', body: { use_llm: true, actor: ACTOR } });
  return getDetail(ref);
}
export const resolve = (ref: string) => req(`/api/incidents/${ref}/resolve`, { method: 'POST', body: { resolved_by: ACTOR, note: 'Resolved from dashboard' } });
export const reportMarkdown = (ref: string) => req<string>(`/api/incidents/${ref}/report?format=md`);
export async function runScenario(code: string): Promise<string> {
  const r = await req(`/api/demo/scenarios/${code}?investigate=true&use_llm=true`, { method: 'POST' });
  return r.incident.reference;
}
export const seedAll = () => req('/api/demo/seed-all', { method: 'POST' });
export async function openIncident(transactionId: string): Promise<string> {
  const r = await req('/api/incidents', { method: 'POST', body: { transaction_id: transactionId.trim() } });
  return r.reference;
}
export const createOrder = (amount: string) => req<{ order_id: string; approve_url: string | null; status: string }>('/api/paypal/orders', { method: 'POST', body: { amount, currency: 'USD', description: 'PaymentOps AI sandbox order' } });
export const orderState = (id: string) => req<{ order_id: string; state: string | null; paypal_status: string; capture_id: string | null; incident: string | null }>(`/api/paypal/orders/${id}`);
export const capture = (id: string, simulateTimeout = false) => req<{ outcome: string; incident: string | null; message?: string; capture_id?: string }>(`/api/paypal/orders/${id}/capture`, { method: 'POST', body: { approved_by: ACTOR, confirm: true, simulate_timeout: simulateTimeout } });
export type LogResult = { method: string; ingested: number; skipped_duplicates: number; rejected: { reason: string; quote: string }[]; events: { event_type: string; timestamp: string; transaction_state: string | null; quote: string }[]; note: string };
export const ingestLog = (incidentRef: string, logText: string) => req<LogResult>(`/api/incidents/${incidentRef}/logs`, { method: 'POST', body: { log_text: logText, use_llm: true } });

// Incidents opened per day over the last 7 days (real data for the overview chart).
export function trendFrom(incidents: Incident[]) {
  const out: { day: string; count: number }[] = [];
  for (let k = 6; k >= 0; k--) {
    const d = new Date(Date.now() - k * 86400000);
    const key = `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
    out.push({ day: d.toLocaleDateString('en-US', { weekday: 'short' }), count: incidents.filter((i) => i.created.startsWith(key)).length });
  }
  return out;
}
