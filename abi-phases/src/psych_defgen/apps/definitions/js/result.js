// Markup for one run: its progress while it works, its error when it failed,
// and its result once it succeeded. Pure functions of the API's data, so
// every optional field can be checked absent. Every value goes through esc().

import {
  esc, externalLink, formatDate, formatNumber, isActive, runDuration, STAGES, stageLabel, statusPill,
} from './ui.js';

// Upstream's file name for a result: re.sub(r"[^a-zA-Z0-9_-]+", "_", term.strip().lower()) + ".json".
export function fileName(term) {
  return `${String(term ?? '').trim().toLowerCase().replace(/[^a-zA-Z0-9_-]+/g, '_')}.json`;
}

export const pubmedUrl = (pmid) => `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(pmid)}/`;
export const pmcUrl = (pmcid) => `https://pmc.ncbi.nlm.nih.gov/articles/${encodeURIComponent(pmcid)}/`;

const present = (value) => value !== null && value !== undefined && String(value).trim() !== '';

const APA = {
  found: ['ok', 'Entry found', (entry) => `The APA Dictionary of Psychology has an entry${present(entry.entry_heading) ? ` headed “${esc(entry.entry_heading)}”` : ''}.`],
  not_found: ['neutral', 'No entry', () => 'The APA Dictionary of Psychology has no entry for this term.'],
  unavailable: ['warn', 'Could not be confirmed', () => 'The APA Dictionary of Psychology did not confirm an entry for this term. It may have none; follow the link to check.'],
  request_error: ['warn', 'Could not be checked', () => 'The APA Dictionary of Psychology could not be reached when this run checked it.'],
};

export function apaHtml(entry) {
  if (!entry || typeof entry !== 'object') {
    return '<p class="muted">The APA Dictionary of Psychology was not checked.</p>';
  }
  const [tone, label, text] = APA[entry.status] || ['neutral', entry.status || 'Unknown', () => ''];
  const link = externalLink(entry.url, entry.status === 'found' ? 'Open the entry on dictionary.apa.org' : 'Look it up on dictionary.apa.org');
  const detail = entry.status !== 'found' && present(entry.error) ? `<small>${esc(entry.error)}</small>` : '';
  return `<p><span class="pill ${tone}">${esc(label)}</span></p>`
    + `${text(entry) ? `<p>${text(entry)}</p>` : ''}${detail}${link ? `<p>${link}</p>` : ''}`;
}

const METHODS = {
  pattern: 'Pattern match',
  llm_selection: 'Selected by LLM',
};

export function definitionHtml(result) {
  const method = METHODS[result.definition_method];
  if (!method) {
    return '<p><span class="pill neutral">None found</span></p>'
      + `<p class="muted">${esc(result.definition || 'No explicit definition was identified in the retrieved literature.')}</p>`;
  }
  return `<p><span class="pill ok">${esc(method)}</span></p>`
    + `<blockquote class="quote">${esc(result.definition)}</blockquote>`
    + `<small>${result.definition_method === 'pattern'
      ? 'Quoted from the literature: a sentence that matches a definition pattern.'
      : 'Quoted from the literature: a sentence an LLM picked out of the evidence, word for word.'}</small>`;
}

export function generatedHtml(result, model) {
  if (!present(result.generated_definition)) {
    return '<p><span class="pill warn">Not enough evidence</span></p>'
      + '<p class="muted">The model found the evidence insufficient to write a definition.</p>';
  }
  return `<p><span class="pill generated">AI-generated</span></p>`
    + `<blockquote class="quote generated-text">${esc(result.generated_definition)}</blockquote>`
    + `<small>Written by ${esc(model || 'an LLM')} from the top evidence passages. It is not a quotation: check it against the evidence below.</small>`;
}

const AUTHORS_SHOWN = 6;

function authorsText(authors) {
  const list = Array.isArray(authors) ? authors.filter(present).map(String) : (present(authors) ? [String(authors)] : []);
  if (list.length <= AUTHORS_SHOWN) return list.join(', ');
  return `${list.slice(0, AUTHORS_SHOWN).join(', ')} et al.`;
}

export function formatScore(score) {
  return score === null || score === undefined || score === '' || !Number.isFinite(Number(score))
    ? '—'
    : Number(score).toFixed(3);
}

export function evidenceHtml(item, index) {
  const authors = authorsText(item.authors);
  const where = [item.journal, item.year].filter(present).map(String).join(' · ');
  const links = [
    present(item.pmid) ? externalLink(pubmedUrl(item.pmid), `PubMed ${item.pmid}`) : '',
    present(item.pmcid) ? externalLink(pmcUrl(item.pmcid), `PMC ${item.pmcid}`) : '',
  ].filter(Boolean);
  const context = present(item.context_text) && item.context_text !== item.text
    ? `<details><summary>Surrounding text</summary><div class="context">${esc(item.context_text)}</div></details>`
    : '';
  return `<li class="passage">`
    + `<div class="passage-head"><span class="rank">${index + 1}</span>`
    + `${present(item.source) ? `<span class="badge">${esc(item.source)}</span>` : ''}`
    + `<span class="score" title="Mean cosine similarity to the definition queries">Score ${esc(formatScore(item.score))}</span></div>`
    + `<p class="passage-text">${esc(item.text)}</p>${context}`
    + `<p class="cite">${present(item.title) ? `<strong>${esc(item.title)}</strong>` : '<strong class="muted">Untitled</strong>'}`
    + `${authors ? `<span>${esc(authors)}</span>` : ''}${where ? `<span>${esc(where)}</span>` : ''}</p>`
    + `${links.length ? `<p class="links">${links.join('')}</p>` : ''}`
    + `</li>`;
}

export function evidenceListHtml(evidence) {
  const items = Array.isArray(evidence) ? evidence.filter((item) => item && typeof item === 'object') : [];
  if (!items.length) return '<p class="muted">No evidence passages were ranked.</p>';
  return `<ol class="passages">${items.map(evidenceHtml).join('')}</ol>`;
}

const COUNTS = [
  ['pubmed_articles', 'PubMed articles'],
  ['pmc_full_texts', 'PMC full texts'],
  ['abstract_fallbacks', 'Abstract fallbacks'],
  ['definition_candidates', 'Definition candidates'],
  ['retrieval_items', 'Passages ranked'],
];

export function countsHtml(counts) {
  if (!counts || typeof counts !== 'object' || !Object.keys(counts).length) return '<p class="muted">Not reported.</p>';
  const known = new Map(COUNTS);
  const keys = [...COUNTS.map(([key]) => key).filter((key) => key in counts), ...Object.keys(counts).filter((key) => !known.has(key))];
  return `<dl class="stats">${keys.map((key) => `<div><dt>${esc(known.get(key) || key)}</dt><dd>${esc(formatNumber(counts[key]))}</dd></div>`).join('')}</dl>`;
}

function usageRow(label, usage, note = '') {
  if (!usage || typeof usage !== 'object') {
    return `<tr><th scope="row">${esc(label)}</th><td colspan="4" class="muted">${esc(note || 'Not reported')}</td></tr>`;
  }
  return `<tr><th scope="row">${esc(label)}</th><td data-label="Model">${esc(usage.model || '—')}</td>`
    + `<td data-label="Input">${esc(formatNumber(usage.input_tokens))}</td><td data-label="Output">${esc(formatNumber(usage.output_tokens))}</td>`
    + `<td data-label="Total">${esc(formatNumber(usage.total_tokens))}</td></tr>`;
}

export function usageHtml(usage, result) {
  if (!usage || typeof usage !== 'object') return '<p class="muted">Not reported.</p>';
  const selectionNote = result.definition_method === 'pattern' ? 'Not needed: a pattern found the definition' : 'No call made';
  return '<div class="table-wrap stacked-wrap"><table class="usage stacked"><caption class="sr-only">Token usage</caption>'
    + '<thead><tr><th scope="col">Step</th><th scope="col">Model</th><th scope="col">Input</th><th scope="col">Output</th><th scope="col">Total</th></tr></thead><tbody>'
    + usageRow('Selection', usage.selection, selectionNote)
    + usageRow('Synthesis', usage.synthesis, 'No call made')
    + usageRow('Total', usage)
    + '</tbody></table></div>'
    + '<small>A dash means the provider did not report that count.</small>';
}

export function modelsHtml(run, result) {
  const rows = [
    ['LLM model', result.llm_usage?.model || run.model],
    ['Embedding model', result.embedding_model || run.embedding_model],
    ['Pipeline', result.upstream],
  ].filter(([, value]) => present(value));
  return `<dl class="details">${rows.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('')}</dl>`;
}

function card(title, body, extra = '') {
  return `<section class="card${extra ? ` ${extra}` : ''}"><h2>${esc(title)}</h2>${body}</section>`;
}

export function resultHtml(run) {
  const result = run.result;
  if (!result || typeof result !== 'object') {
    return '<p class="card muted">This run succeeded but its result is not available.</p>';
  }
  const evidenceCount = Array.isArray(result.evidence) ? result.evidence.length : 0;
  return card('Definition from the literature', definitionHtml(result))
    + card('AI-synthesized definition', generatedHtml(result, result.llm_usage?.synthesis?.model || result.llm_usage?.model || run.model), 'generated')
    + card('APA Dictionary of Psychology', apaHtml(result.apa_entry))
    + card(`Evidence${evidenceCount ? ` (${evidenceCount})` : ''}`, evidenceListHtml(result.evidence))
    + `<div class="grid">${card('What was read', countsHtml(result.counts))}${card('Models', modelsHtml(run, result))}</div>`
    + card('Token usage', usageHtml(result.llm_usage, result));
}

export function progressHtml(run) {
  const index = STAGES.findIndex(([key]) => key === run.stage);
  const current = run.status === 'running' ? index : -1;
  const steps = STAGES.map(([key, label], i) => {
    const state = current === -1 ? 'todo' : (i < current ? 'done' : (i === current ? 'current' : 'todo'));
    return `<li class="${state}"${state === 'current' ? ' aria-current="step"' : ''}>${esc(label)}</li>`;
  }).join('');
  return card('Progress', `<p class="stage" role="status">${esc(stageLabel(run))}</p>`
    + `<ol class="stages">${steps}</ol>`
    + '<small>A run reads up to hundreds of articles and usually takes a few minutes. This page updates every 5 seconds.</small>');
}

export function failureHtml(run) {
  return card('The run failed', `<p class="failure">${esc(run.error || 'No reason was recorded.')}</p>`
    + '<small>Start a new run from the form to try again.</small>', 'failed');
}

export function summaryHtml(run, now = Date.now()) {
  const rows = [
    ['Status', statusPill(run.status) + (isActive(run) ? ` <span class="muted">${esc(stageLabel(run))}</span>` : '')],
    ['Model', esc(run.model || '—')],
    ['Max results', esc(formatNumber(run.max_results))],
    ['Top-k', esc(formatNumber(run.top_k))],
    ['Requested', esc(formatDate(run.created_at))],
    ['Started', esc(formatDate(run.started_at, 'Not yet'))],
    ['Finished', esc(formatDate(run.finished_at, isActive(run) ? 'Not yet' : '—'))],
    ['Duration', esc(runDuration(run, now))],
  ];
  return `<dl class="details">${rows.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${value}</dd>`).join('')}</dl>`;
}

export function runBodyHtml(run) {
  if (isActive(run)) return progressHtml(run);
  if (run.status === 'failed') return failureHtml(run);
  if (run.status === 'succeeded') return resultHtml(run);
  return `<p class="card muted">This run is in an unknown state (${esc(run.status)}).</p>`;
}
