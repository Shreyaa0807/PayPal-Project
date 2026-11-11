// Types shared by the UI. The mock fixtures were removed: all data now comes from the backend (see api.ts).
export type Severity = 'Critical' | 'High' | 'Medium' | 'Low';
export type State = 'Open' | 'Investigating' | 'Resolved';
export type Event = { id: string; time: string; title: string; detail: string; source: string; kind: 'success' | 'warning' | 'error' | 'info'; fact: boolean };
export type Incident = {
  id: string; order: string; capture: string; type: string; merchant: string; amount: number; currency: string;
  severity: Severity; status: State; payment: 'Completed' | 'Pending' | 'Failed' | 'Unknown'; created: string; updated: string;
  confidence: number; confidenceLabel: string; generatedBy: string; agentSteps: { step: string; status: string; detail: string }[];
  summary: string; rootCause: string; recommendation: string; prohibited: string[]; evidence: string[]; contradictions: string[];
  alternatives: string[]; missing: string[]; events: Event[]; scenario: string; source: 'Synthetic fixture' | 'PayPal Sandbox';
};

export const EMPTY_INCIDENT: Incident = {
  id: '', order: '', capture: '—', type: 'No incident selected', merchant: 'Northstar Commerce', amount: 0, currency: 'USD',
  severity: 'Low', status: 'Open', payment: 'Unknown', created: '—', updated: '—', confidence: 0, confidenceLabel: '—', generatedBy: 'Not run yet', agentSteps: [],
  summary: 'No incidents yet. Seed the demo scenarios or create a sandbox payment in the Demo Lab.', rootCause: '—', recommendation: '—',
  prohibited: [], evidence: [], contradictions: [], alternatives: [], missing: [], events: [], scenario: '', source: 'Synthetic fixture',
};
