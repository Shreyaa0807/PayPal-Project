import { useEffect } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  BrainCircuit,
  Check,
  CheckCircle2,
  Clock3,
  Code2,
  FileCheck2,
  Fingerprint,
  Github,
  Layers3,
  LockKeyhole,
  Play,
  Radio,
  ShieldCheck,
  Sparkles,
  Webhook,
} from 'lucide-react';
import './landing.css';

type LandingProps = {
  onOpenDemo: () => void;
};

const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;
const videoId = env?.VITE_DEMO_VIDEO_ID;
const githubUrl = 'https://github.com/Shreyaa0807/PayPal-Project';

const problems = [
  {
    icon: Clock3,
    title: 'The timeout',
    text: 'A request times out. Did the payment fail—or did the response?',
  },
  {
    icon: AlertTriangle,
    title: 'The dangerous retry',
    text: 'Retrying blind can turn an unknown outcome into a duplicate charge.',
  },
  {
    icon: Layers3,
    title: 'The evidence gap',
    text: 'Logs, webhooks and dashboards disagree while support loses hours.',
  },
];

const steps = [
  ['01', 'Collect evidence', 'PayPal API responses, verified webhooks and merchant logs.', Radio],
  ['02', 'Reconstruct the timeline', 'See confirmed facts alongside clearly marked inferences.', Activity],
  ['03', 'Investigate with AI', 'Get a root cause, cited evidence and a recommended action.', BrainCircuit],
  ['04', 'Make the safe move', 'Deterministic rules approve or block. Humans approve captures.', ShieldCheck],
] as const;

const features = [
  [BrainCircuit, 'Agentic state check', 'Checks the PayPal order before anyone reaches for retry.'],
  [Activity, 'Evidence-first timeline', 'Understand what happened, and what is still only inferred.'],
  [Code2, 'AI log reader', 'Extracted events must quote the source log. No invented evidence.'],
  [ShieldCheck, 'Deterministic safety', 'Rules—not a language model—decide which actions are allowed.'],
  [Webhook, 'Duplicate-aware webhooks', 'Repeated deliveries are recognized and processed safely.'],
  [FileCheck2, 'Exportable incident reports', 'Share a readable record of findings, evidence and next steps.'],
] as const;

const trustPoints = [
  'Only PayPal API responses and signature-verified webhooks count as proof.',
  'Pasted logs cannot change confirmed payment state.',
  'Emails, tokens and card-like numbers are redacted before AI analysis.',
  'Confidence reflects evidence quality—not the model’s guess.',
  'The safety engine keeps working when AI is unavailable.',
];

const technologies = [
  'PayPal Sandbox',
  'FastAPI',
  'Neon Postgres',
  'OpenRouter',
  'React',
  'TypeScript',
  'AG Grid',
  'Render',
  'Vercel',
];

function Logo() {
  return (
    <a className="landing-brand" href="#top" aria-label="PaymentOps AI home">
      <span className="landing-brand-mark" aria-hidden="true">
        <span>P</span>
        <span>P</span>
      </span>
      <span className="landing-brand-name">
        PaymentOps <span>AI</span>
      </span>
    </a>
  );
}

function InvestigationPreview() {
  return (
    <div className="preview-window" aria-label="Preview of a payment investigation">
      <div className="preview-topbar">
        <div className="preview-dots" aria-hidden="true"><i /><i /><i /></div>
        <span>paymentops.ai / investigations / INC-008421</span>
        <span className="preview-live"><i /> SANDBOX</span>
      </div>
      <div className="preview-content">
        <div className="preview-heading">
          <div>
            <span className="preview-eyebrow">INVESTIGATION · CAPTURE TIMEOUT</span>
            <h3>Payment outcome is unknown</h3>
            <p>Order <code>PAY-7KX29104</code> · $84.00 USD</p>
          </div>
          <span className="preview-status"><i /> Needs review</span>
        </div>
        <div className="preview-finding">
          <span className="finding-icon"><Sparkles size={16} /></span>
          <div>
            <strong>PayPal confirms the capture completed.</strong>
            <p>The merchant request timed out, but the verified capture response arrived afterward.</p>
          </div>
        </div>
        <div className="preview-timeline">
          <div className="timeline-row">
            <span className="timeline-marker confirmed"><Check size={12} /></span>
            <span className="timeline-time">10:42:08</span>
            <span><strong>Capture completed</strong><small>PayPal API · Confirmed</small></span>
            <span className="timeline-tag">VERIFIED</span>
          </div>
          <div className="timeline-row">
            <span className="timeline-marker inferred"><ArrowDownRight size={12} /></span>
            <span className="timeline-time">10:42:38</span>
            <span><strong>Merchant request timed out</strong><small>Application log · Supporting evidence</small></span>
            <span className="timeline-tag inferred-tag">LOG</span>
          </div>
        </div>
        <div className="preview-do-not">
          <div className="do-not-title"><LockKeyhole size={15} /> DO NOT</div>
          <div className="do-not-item"><span>×</span> Capture this order again</div>
          <div className="do-not-item"><span>×</span> Blindly retry the payment</div>
          <div className="do-not-item"><span>×</span> Mark the order as failed</div>
        </div>
        <div className="preview-footer"><ShieldCheck size={14} /> Safety rules checked <span>·</span> AI recommendation backed by evidence</div>
      </div>
    </div>
  );
}

export function Landing({ onOpenDemo }: LandingProps) {
  useEffect(() => {
    const items = document.querySelectorAll<HTMLElement>('.landing-page [data-reveal]');
    const observer = new IntersectionObserver(
      entries => entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      }),
      { threshold: 0.12, rootMargin: '0px 0px -36px 0px' },
    );

    items.forEach(item => observer.observe(item));
    return () => observer.disconnect();
  }, []);

  return (
    <div className="landing-page" id="top">
      <header className="landing-nav">
        <div className="landing-nav-inner">
          <Logo />
          <nav className="landing-links" aria-label="Main navigation">
            <a href="#how-it-works">How it works</a>
            <a href="#safety">Safety</a>
            <a href="#tech">Tech</a>
            <a href={githubUrl} target="_blank" rel="noreferrer">GitHub <Github size={14} /></a>
          </nav>
          <button className="landing-button landing-button-primary nav-demo-button" onClick={onOpenDemo}>
            Open live demo <ArrowRight size={16} />
          </button>
        </div>
      </header>

      <main>
        <section className="landing-hero">
          <div className="landing-hero-glow" aria-hidden="true" />
          <div className="landing-container hero-layout">
            <div className="hero-copy" data-reveal>
              <div className="hero-label"><span className="hero-label-dot" /> PAYMENT INCIDENT INTELLIGENCE</div>
              <h1>Payment failed?<br /><span>Don’t retry.</span> Investigate.</h1>
              <p className="hero-subline">PaymentOps AI reconstructs what really happened to a payment from PayPal evidence, explains it, and blocks unsafe retries.</p>
              <div className="hero-actions">
                <button className="landing-button landing-button-primary" onClick={onOpenDemo}>
                  Open live demo <ArrowRight size={17} />
                </button>
                <a className="landing-button landing-button-quiet" href="#video">
                  <span className="play-icon"><Play size={13} fill="currentColor" /></span> Watch the 3-minute video
                </a>
              </div>
              <div className="hero-proof"><span className="proof-check"><Check size={12} /></span> Built on the PayPal Sandbox <span className="proof-separator">·</span> Test funds only</div>
            </div>
            <div className="hero-visual" data-reveal>
              <div className="visual-orbit orbit-a" aria-hidden="true" />
              <div className="visual-orbit orbit-b" aria-hidden="true" />
              <div className="visual-glint" aria-hidden="true"><Sparkles size={17} /></div>
              <InvestigationPreview />
              <div className="visual-caption"><span className="caption-dot" /> Evidence-led. Retry-safe. Human-approved.</div>
            </div>
          </div>
          <div className="hero-bottom-line" aria-hidden="true" />
        </section>

        <section className="section problem-section">
          <div className="landing-container">
            <div className="section-intro centered" data-reveal>
              <span className="section-kicker">THE MOMENT THAT MATTERS</span>
              <h2>One timeout. Three very different risks.</h2>
              <p>When payment signals disagree, guessing is the most expensive option.</p>
            </div>
            <div className="problem-grid">
              {problems.map(({ icon: Icon, title, text }, index) => (
                <article className="problem-card" data-reveal key={title}>
                  <span className={`problem-icon problem-icon-${index}`}><Icon size={20} /></span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                  <span className="problem-index">0{index + 1}</span>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="section workflow-section" id="how-it-works">
          <div className="landing-container">
            <div className="section-intro" data-reveal>
              <span className="section-kicker">FROM SIGNAL TO SAFE ACTION</span>
              <h2>Clarity before the next click.</h2>
              <p>Every recommendation starts with the evidence—and ends with a safety check.</p>
            </div>
            <div className="workflow-grid">
              {steps.map(([number, title, text, Icon], index) => (
                <article className="workflow-step" data-reveal key={number}>
                  <div className="step-top"><span className="step-number">{number}</span><span className="step-icon"><Icon size={20} /></span></div>
                  <h3>{title}</h3>
                  <p>{text}</p>
                  {index < steps.length - 1 && <ArrowRight className="step-connector" size={18} aria-hidden="true" />}
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="section features-section">
          <div className="landing-container">
            <div className="section-intro centered" data-reveal>
              <span className="section-kicker">BUILT FOR THE MESSY MIDDLE</span>
              <h2>Everything an investigation needs.</h2>
            </div>
            <div className="features-grid">
              {features.map(([Icon, title, text]) => (
                <article className="feature-card" data-reveal key={title}>
                  <span className="feature-icon"><Icon size={19} /></span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                  <span className="feature-arrow"><ArrowRight size={15} /></span>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="safety-section" id="safety">
          <div className="landing-container safety-layout">
            <div className="safety-copy" data-reveal>
              <span className="section-kicker">THE RULE THAT MATTERS MOST</span>
              <h2>AI proposes.<br /><span>Code decides.</span></h2>
              <p>Payment decisions deserve more than a convincing answer. Every AI finding is checked against deterministic safety rules.</p>
              <div className="safety-quote"><span>“</span><strong>AI proposes, code decides.</strong></div>
            </div>
            <div className="safety-card" data-reveal>
              <div className="safety-card-header"><span className="safety-shield"><ShieldCheck size={19} /></span><div><strong>Safety is not a prompt.</strong><small>It’s enforced in the product.</small></div><span className="safety-indicator"><i /> ACTIVE</span></div>
              <ul>
                {trustPoints.map(point => <li key={point}><CheckCircle2 size={17} /><span>{point}</span></li>)}
              </ul>
              <div className="safety-card-foot"><Fingerprint size={15} /> Evidence-backed decisions, with a human in the loop.</div>
            </div>
          </div>
        </section>

        <section className="section tech-section" id="tech">
          <div className="landing-container tech-inner">
            <div className="tech-copy" data-reveal><span className="section-kicker">THE STACK</span><h2>Practical tech. Purpose-built safety.</h2></div>
            <div className="tech-list">{technologies.map((technology, index) => <span className="tech-chip" data-reveal key={technology}><i className={`tech-mark tech-mark-${index}`} />{technology}</span>)}</div>
          </div>
        </section>

        <section className="section video-section" id="video">
          <div className="landing-container">
            <div className="video-heading">
              <div data-reveal><span className="section-kicker">SEE THE INVESTIGATION</span><h2>From timeout to “do not retry.”</h2></div>
              <p>A three-minute walkthrough of an evidence-led payment investigation.</p>
            </div>
            <div className="video-frame" data-reveal>
              {videoId ? (
                <iframe
                  src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?rel=0`}
                  title="PaymentOps AI three-minute demo"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                  referrerPolicy="strict-origin-when-cross-origin"
                  allowFullScreen
                />
              ) : (
                <div className="video-placeholder">
                  <div className="video-placeholder-glow" />
                  <span className="video-play"><Play size={20} fill="currentColor" /></span>
                  <strong>See PaymentOps AI in action</strong>
                  <span>Set <code>VITE_DEMO_VIDEO_ID</code> to embed your YouTube walkthrough.</span>
                  <a href={githubUrl} target="_blank" rel="noreferrer">Explore the project <ArrowRight size={15} /></a>
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="landing-cta">
          <div className="landing-container cta-inner" data-reveal>
            <div className="cta-orb" aria-hidden="true" />
            <div className="cta-copy"><span className="section-kicker">READY WHEN YOU ARE</span><h2>See it investigate a<br />real sandbox payment.</h2><p>No real money. Just better answers when the signals don’t line up.</p></div>
            <div className="cta-action">
              <button className="landing-button landing-button-light" onClick={onOpenDemo}>Open live demo <ArrowRight size={17} /></button>
              <span><Check size={13} /> Sandbox only. Test funds. Synthetic scenarios are labelled.</span>
            </div>
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <div className="landing-container footer-top">
          <div className="footer-brand"><Logo /><p>Investigate first. Retry safely.</p></div>
          <div className="footer-links"><a href={githubUrl} target="_blank" rel="noreferrer"><Github size={15} /> GitHub</a><a href={`${githubUrl}/blob/main/LICENSE`} target="_blank" rel="noreferrer">MIT License <ArrowRight size={13} /></a><a href="#top">Back to top ↑</a></div>
        </div>
        <div className="landing-container footer-bottom"><span>Built by Amna &amp; Shreya <span className="footer-dot">·</span> Built for the PayPal AI Hackathon 2026</span><span>Not affiliated with PayPal.</span></div>
      </footer>
    </div>
  );
}
