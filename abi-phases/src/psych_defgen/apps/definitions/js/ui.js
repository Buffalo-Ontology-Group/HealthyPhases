// Shared UI helpers. Every value from the API reaches markup through esc():
// evidence is literature text and the term is user input.

export const $ = (id) => document.getElementById(id);

export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

// Only http(s) links leave the app; anything else (javascript:, data:) is dropped.
export function safeUrl(value) {
  const text = String(value ?? '').trim();
  return /^https?:\/\//i.test(text) ? text : '';
}

export function externalLink(url, label) {
  const target = safeUrl(url);
  return target ? `<a href="${esc(target)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : '';
}

// "A", "A and B", "A, B and C".
export function listText(items) {
  const values = [...items].map(String);
  if (values.length <= 1) return values.join('');
  return `${values.slice(0, -1).join(', ')} and ${values.at(-1)}`;
}

export function formatNumber(value, fallback = '—') {
  return value === null || value === undefined || value === '' || !Number.isFinite(Number(value))
    ? fallback
    : Number(value).toLocaleString();
}

export function formatDate(value, fallback = '—') {
  if (!value) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

export function formatDuration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${seconds % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export const ACTIVE = new Set(['pending', 'running']);
export const isActive = (run) => ACTIVE.has(run?.status);

// How long the run has been (or was) working: from its start, or its request
// while the worker has not reported a start yet, to its end or now.
export function runDuration(run, now = Date.now()) {
  if (!run || run.status === 'pending') return '—';
  const start = Date.parse(run.started_at || run.created_at || '');
  const end = run.finished_at ? Date.parse(run.finished_at) : (run.status === 'running' ? now : NaN);
  return formatDuration(end - start);
}

export const STAGES = [
  ['apa', 'Checking the APA Dictionary'],
  ['search', 'Searching PubMed'],
  ['full_text', 'Reading full texts'],
  ['ranking', 'Ranking the evidence'],
  ['selection', 'Selecting a definition from the literature'],
  ['synthesis', 'Writing the AI-synthesized definition'],
];
const STAGE_LABELS = Object.fromEntries(STAGES);

export function stageLabel(run) {
  if (run?.status === 'pending') return 'Waiting to start';
  if (run?.status !== 'running') return '';
  return STAGE_LABELS[run.stage] || 'Starting';
}

const STATUS = {
  pending: ['neutral', 'Pending'],
  running: ['progress', 'Running'],
  succeeded: ['ok', 'Succeeded'],
  failed: ['bad', 'Failed'],
};

export function statusPill(status) {
  const [tone, label] = STATUS[status] || ['neutral', status || 'Unknown'];
  return `<span class="pill ${tone}">${esc(label)}</span>`;
}

export function message(target, text = '', kind = '') {
  const el = typeof target === 'string' ? $(target) : target;
  if (!el) return;
  el.textContent = text;
  el.className = `msg ${kind}`.trim();
}

// A counter that lets a late response notice it has been superseded.
export function latest() {
  let version = 0;
  return { next: () => ++version, is: (token) => token === version };
}

export const TIMERS = {
  set: (work, ms) => setTimeout(work, ms),
  clear: (handle) => clearTimeout(handle),
};

// Calls `work` once, `ms` after schedule(), unless stopped first. Each round
// schedules the next one itself, so a slow answer never overlaps the next poll,
// and nothing is scheduled while the page is hidden.
export function poller(work, { ms = 5000, timers = TIMERS } = {}) {
  let handle = null;
  return {
    get active() { return handle !== null; },
    schedule() {
      if (handle !== null || document.hidden) return;
      handle = timers.set(() => { handle = null; work(); }, ms);
    },
    stop() {
      if (handle !== null) timers.clear(handle);
      handle = null;
    },
  };
}
