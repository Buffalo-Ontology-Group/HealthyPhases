// A minimal browser stand-in for the Construct Definitions modules, and a fake
// /psych_defgen/api that follows the HTTP contract.
//
// Elements are created on first lookup by id, so index.html does not need
// parsing: the module finds the same element objects the test inspects.
// Each install() replaces the globals, giving every test a clean page.

const { join } = require('node:path');
const { pathToFileURL } = require('node:url');

const ROOT = join(__dirname, '..');

function element(tag = 'div') {
  const el = {
    tagName: tag.toUpperCase(), id: '', value: '', textContent: '', innerHTML: '', className: '', href: '',
    hidden: false, disabled: false, selected: false, min: '', max: '',
    children: [], options: [], dataset: {}, attributes: {}, listeners: {},
    replaceChildren() { this.children = []; this.options = []; this.innerHTML = ''; },
    append(...nodes) {
      for (const node of nodes) {
        this.children.push(node);
        if (node && node.tagName === 'OPTION') this.options.push(node);
        if (node && typeof node === 'object') node.parent = this;
      }
    },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); },
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
    setAttribute(key, value) { this.attributes[key] = String(value); },
    getAttribute(key) { return this.attributes[key] ?? null; },
    removeAttribute(key) { delete this.attributes[key]; },
    focus() { el.focused = true; if (globalThis.document) globalThis.document.activeElement = el; },
    contains(node) {
      for (let at = node; at; at = at.parent) if (at === this) return true;
      return false;
    },
    // Only run links: one element per data-run-id in innerHTML, the same
    // objects until innerHTML changes, as the browser keeps its nodes.
    querySelectorAll(selector) {
      if (selector !== 'a[data-run-id]') return [];
      if (this.parsed?.html !== this.innerHTML) {
        const links = [...this.innerHTML.matchAll(/data-run-id="([^"]*)"/g)].map(([, id]) => {
          const link = element('a');
          link.dataset.runId = id.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
          link.parent = this;
          return link;
        });
        this.parsed = { html: this.innerHTML, links };
      }
      return this.parsed.links;
    },
    click() { el.clicked = true; },
  };
  return el;
}

function fire(el, type, event = {}) {
  const full = { preventDefault() {}, target: el, ...event };
  return Promise.all((el.listeners[type] || []).map((handler) => handler(full)));
}

function response(payload, { status = 200 } = {}) {
  return {
    ok: status >= 200 && status < 300, status,
    text: async () => (typeof payload === 'string' ? payload : JSON.stringify(payload)),
  };
}

function install({ hash = '', storage = {}, session = {} } = {}) {
  const elements = new Map();
  const documentListeners = {};
  const document = {
    hidden: false, title: '', activeElement: null,
    body: element('body'), documentElement: element('html'),
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, Object.assign(element(), { id }));
      return elements.get(id);
    },
    createElement: element,
    addEventListener(type, handler) { (documentListeners[type] ||= []).push(handler); },
  };
  const location = { hash };
  const history = { replaceState(_state, _title, url) { location.hash = url; } };
  const windowListeners = {};
  const win = { addEventListener(type, handler) { (windowListeners[type] ||= []).push(handler); } };
  win.parent = win;
  win.top = win;
  const store = (data) => ({
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => { data[key] = String(value); },
  });
  const calls = [];
  Object.assign(globalThis, {
    document, location, history, window: win,
    localStorage: store(storage), sessionStorage: store(session),
    // Requests stay pending unless a test answers them.
    fetch: (url, options = {}) => { calls.push({ url, options }); return new Promise(() => {}); },
  });
  return {
    document, location, history, window: win, calls, elements, session,
    $: (id) => document.getElementById(id),
    // Sets document.hidden and tells the listeners, as the browser does.
    visibility(hidden) {
      document.hidden = hidden;
      (documentListeners.visibilitychange || []).forEach((h) => h({}));
    },
    hashchange: () => (windowListeners.hashchange || []).forEach((h) => h({})),
  };
}

// Timers that run only when the test says so.
function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    pending,
    set(work, ms) { const id = next++; pending.set(id, { work, ms }); return id; },
    clear(id) { pending.delete(id); },
    get count() { return pending.size; },
    get delays() { return [...pending.values()].map((t) => t.ms); },
    // Fires every timer pending now (not the ones they schedule).
    async fire() {
      const due = [...pending.entries()];
      for (const [id, { work }] of due) { pending.delete(id); work(); }
      await settle();
    },
  };
}

function context(overrides = {}) {
  const ctx = {
    navigated: [], saved: [],
    timers: fakeTimers(),
    navigate(target) { ctx.navigated.push(target); },
    save(text, name) { ctx.saved.push({ text, name }); },
    ...overrides,
  };
  return ctx;
}

// Answers fetches by URL: routes maps a substring (optionally "METHOD substring")
// to a payload, a function, or a response(); the longest match wins.
function routeFetch(routes, calls = []) {
  return async (url, options = {}) => {
    const method = options.method || 'GET';
    calls.push({ url, method, body: options.body === undefined ? undefined : JSON.parse(options.body), headers: options.headers, credentials: options.credentials });
    const key = Object.keys(routes)
      .filter((pattern) => {
        const [verb, path] = pattern.includes(' ') ? pattern.split(' ') : [null, pattern];
        return (!verb || verb === method) && url.includes(path);
      })
      .sort((a, b) => b.length - a.length)[0];
    if (key === undefined) throw new Error(`Unexpected request: ${method} ${url}`);
    const value = routes[key];
    const result = typeof value === 'function' ? await value(url, options) : value;
    return result && typeof result.text === 'function' ? result : response(result);
  };
}

const SETTINGS = {
  models: ['openai/gpt-4.1-mini', 'openai/gpt-4.1'],
  default_model: 'openai/gpt-4.1-mini',
  embedding_model: 'text-embedding-3-small',
  max_results: { default: 100, min: 1, max: 200 },
  top_k: { default: 10, min: 1, max: 20 },
  missing: [],
};

function run(overrides = {}) {
  return {
    run_id: 'run-1', term: 'loneliness', max_results: 100, top_k: 10, model: 'openai/gpt-4.1-mini',
    embedding_model: 'text-embedding-3-small', status: 'pending', stage: null, error: null,
    created_at: '2026-11-16T10:00:00Z', started_at: null, finished_at: null, result: null,
    ...overrides,
  };
}

function result(overrides = {}) {
  return {
    term: 'loneliness',
    apa_entry: {
      term: 'loneliness', source: 'APA Dictionary of Psychology', url: 'https://dictionary.apa.org/loneliness',
      status: 'found', entry_heading: 'loneliness', page_title: 'APA Dictionary of Psychology', http_status: 200, error: null,
    },
    definition: 'Loneliness is defined as the distressing feeling that accompanies perceived social isolation.',
    definition_method: 'pattern',
    generated_definition: 'Loneliness is a subjective, aversive state arising from a gap between desired and actual social connection.',
    llm_usage: {
      provider: 'openrouter', model: 'openai/gpt-4.1-mini', input_tokens: 1200, output_tokens: 80, total_tokens: 1280,
      selection: null,
      synthesis: { provider: 'openrouter', model: 'openai/gpt-4.1-mini', input_tokens: 1200, output_tokens: 80, total_tokens: 1280 },
    },
    evidence: [
      {
        text: 'Loneliness is defined as the distressing feeling that accompanies perceived social isolation.',
        context_text: 'Background. Loneliness is defined as the distressing feeling that accompanies perceived social isolation. It is common.',
        title: 'Loneliness and health', authors: ['Ada Smith', 'Bo Chen'], journal: null, year: null,
        pmid: '123456', pmcid: 'PMC7654321', source: 'PMC full text',
        pubmed_url: 'https://pubmed.ncbi.nlm.nih.gov/123456/', url: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC7654321/', score: 0.61234,
      },
      {
        text: 'Loneliness reflects a discrepancy between desired and achieved relationships.',
        context_text: 'Loneliness reflects a discrepancy between desired and achieved relationships.',
        title: 'A review', authors: ['Cy Doe'], journal: 'Psychological Review', year: '2019',
        pmid: '999', pmcid: null, source: 'PubMed abstract',
        pubmed_url: 'https://pubmed.ncbi.nlm.nih.gov/999/', url: null, score: 0.5,
      },
    ],
    pmids: ['123456', '999'],
    pmid_to_pmcid: { 123456: 'PMC7654321' },
    embedding_model: 'text-embedding-3-small',
    counts: { pubmed_articles: 100, pmc_full_texts: 40, abstract_fallbacks: 60, definition_candidates: 12, retrieval_items: 900 },
    upstream: 'psych-defgen 0.0.1b1 (a625984)',
    ...overrides,
  };
}

const load = (relative) => import(pathToFileURL(join(ROOT, 'js', relative)).href);
const tick = () => new Promise((resolve) => setImmediate(resolve));
const settle = async (rounds = 5) => { for (let i = 0; i < rounds; i += 1) await tick(); };

module.exports = {
  ROOT, SETTINGS, context, element, fakeTimers, fire, install, load, response, result, routeFetch, run, settle, tick,
};
