// One run: each status, a full result, absent optional fields, escaping and the download.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { context, fire, install, load, response, result, routeFetch, run, settle } = require('./harness.cjs');

async function view(answer, runId = 'run-1') {
  const dom = install();
  const calls = [];
  globalThis.fetch = routeFetch({ '/psych_defgen/api/runs/': answer }, calls);
  const ctx = context();
  const { createRunView } = await load('run.js');
  const page = createRunView(ctx);
  await page.show({ name: 'run', runId });
  await settle();
  return { dom, ctx, page, calls, $: dom.$, timers: ctx.timers };
}

const succeeded = (overrides = {}, runOverrides = {}) => run({
  status: 'succeeded', started_at: '2026-11-16T10:00:02Z', finished_at: '2026-11-16T10:04:02Z', result: result(overrides), ...runOverrides,
});

test('a pending run says it is waiting and polls every 5 s', async () => {
  const { $, page, timers, calls } = await view(run());
  assert.match(calls[0].url, /\/psych_defgen\/api\/runs\/run-1$/);
  assert.equal($('run-title').textContent, 'loneliness');
  assert.match($('run-summary').innerHTML, /<span class="pill neutral">Pending<\/span>/);
  assert.match($('run-body').innerHTML, /<p class="stage" role="status">Waiting to start<\/p>/);
  assert.equal($('run-download').hidden, true);
  assert.equal(page.polling, true);
  assert.deepEqual(timers.delays, [5000]);
});

test('a running run names its stage in words and marks the steps done', async () => {
  const { STAGES } = await load('ui.js');
  const labels = {
    apa: 'Checking the APA Dictionary', search: 'Searching PubMed', full_text: 'Reading full texts',
    ranking: 'Ranking the evidence', selection: 'Selecting a definition from the literature', synthesis: 'Writing the AI-synthesized definition',
  };
  assert.deepEqual(Object.fromEntries(STAGES), labels);
  const { $ } = await view(run({ status: 'running', stage: 'ranking', started_at: '2026-11-16T10:00:02Z' }));
  const html = $('run-body').innerHTML;
  assert.match(html, /role="status">Ranking the evidence</);
  assert.match(html, /<li class="done">Reading full texts<\/li><li class="current" aria-current="step">Ranking the evidence<\/li><li class="todo">/);
  assert.match($('run-summary').innerHTML, /Running/);
});

test('polling follows the run until it succeeds, then stops and offers the download', async () => {
  let state = run({ status: 'running', stage: 'search' });
  const { $, page, timers } = await view(() => state);
  state = run({ status: 'running', stage: 'synthesis' });
  await timers.fire();
  assert.match($('run-body').innerHTML, /Writing the AI-synthesized definition/);
  assert.equal(timers.count, 1);
  state = succeeded();
  await timers.fire();
  assert.equal(page.polling, false);
  assert.equal(timers.count, 0);
  assert.equal($('run-download').hidden, false);
  assert.match($('run-body').innerHTML, /Definition from the literature/);
});

test('a failed run shows its error and does not poll', async () => {
  const { $, page } = await view(run({ status: 'failed', error: 'NCBI did not answer: try again later.', finished_at: '2026-11-16T10:01:00Z' }));
  assert.match($('run-body').innerHTML, /<p class="failure">NCBI did not answer: try again later\.<\/p>/);
  assert.match($('run-summary').innerHTML, /<span class="pill bad">Failed<\/span>/);
  assert.equal(page.polling, false);
  assert.equal($('run-download').hidden, true);
});

test('a failed run without a reason says none was recorded', async () => {
  const { $ } = await view(run({ status: 'failed' }));
  assert.match($('run-body').innerHTML, /No reason was recorded/);
});

test('a full result shows every part', async () => {
  const { $ } = await view(succeeded());
  const html = $('run-body').innerHTML;
  // APA: status and link, never APA text.
  assert.match(html, /<span class="pill ok">Entry found<\/span>/);
  assert.match(html, /headed “loneliness”/);
  assert.match(html, /<a href="https:\/\/dictionary\.apa\.org\/loneliness" target="_blank" rel="noopener noreferrer">Open the entry on dictionary\.apa\.org<\/a>/);
  // The definition from the literature, with its method.
  assert.match(html, /<span class="pill ok">Pattern match<\/span><\/p><blockquote class="quote">Loneliness is defined as the distressing feeling/);
  // The generated definition, labelled as such.
  assert.match(html, /<h2>AI-synthesized definition<\/h2><p><span class="pill generated">AI-generated<\/span>/);
  assert.match(html, /Written by openai\/gpt-4\.1-mini from the top evidence passages\. It is not a quotation/);
  // Evidence: text, title, authors, journal and year, links built from the IDs, source and score.
  assert.match(html, /<h2>Evidence \(2\)<\/h2>/);
  assert.match(html, /<strong>Loneliness and health<\/strong><span>Ada Smith, Bo Chen<\/span>/);
  assert.match(html, /<span>Psychological Review · 2019<\/span>/);
  assert.match(html, /href="https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/123456\/"[^>]*>PubMed 123456</);
  assert.match(html, /href="https:\/\/pmc\.ncbi\.nlm\.nih\.gov\/articles\/PMC7654321\/"[^>]*>PMC PMC7654321</);
  assert.match(html, /<span class="badge">PMC full text<\/span>/);
  assert.match(html, /<span class="badge">PubMed abstract<\/span>/);
  assert.match(html, /Score 0\.612</);
  assert.match(html, /Score 0\.500</);
  assert.match(html, /<summary>Surrounding text<\/summary><div class="context">Background\./);
  // Counts, token usage and models.
  assert.match(html, /<dt>PubMed articles<\/dt><dd>100<\/dd>/);
  assert.match(html, /<dt>Passages ranked<\/dt><dd>900<\/dd>/);
  assert.match(html, /<th scope="row">Selection<\/th><td colspan="4" class="muted">Not needed: a pattern found the definition<\/td>/);
  assert.match(html, /<th scope="row">Synthesis<\/th><td data-label="Model">openai\/gpt-4\.1-mini<\/td><td data-label="Input">1,200<\/td><td data-label="Output">80<\/td><td data-label="Total">1,280<\/td>/);
  assert.match(html, /<dt>LLM model<\/dt><dd>openai\/gpt-4\.1-mini<\/dd><dt>Embedding model<\/dt><dd>text-embedding-3-small<\/dd><dt>Pipeline<\/dt><dd>psych-defgen 0\.0\.1b1 \(a625984\)<\/dd>/);
  assert.match($('run-summary').innerHTML, /<dt>Duration<\/dt><dd>4 min 0 s<\/dd>/);
});

test('an LLM-selected definition and a missing generated one are worded as such', async () => {
  const { $ } = await view(succeeded({
    definition_method: 'llm_selection',
    generated_definition: null,
    llm_usage: { provider: 'openrouter', model: 'm', input_tokens: null, output_tokens: null, total_tokens: null,
      selection: { model: 'm', input_tokens: 10, output_tokens: 2, total_tokens: 12 }, synthesis: null },
  }));
  const html = $('run-body').innerHTML;
  assert.match(html, /<span class="pill ok">Selected by LLM<\/span>/);
  assert.match(html, /<span class="pill warn">Not enough evidence<\/span>/);
  assert.doesNotMatch(html, /AI-generated/);
  assert.match(html, /<th scope="row">Selection<\/th><td data-label="Model">m<\/td><td data-label="Input">10<\/td><td data-label="Output">2<\/td><td data-label="Total">12<\/td>/);
  assert.match(html, /<th scope="row">Total<\/th><td data-label="Model">m<\/td><td data-label="Input">—<\/td><td data-label="Output">—<\/td><td data-label="Total">—<\/td>/);
});

test('no definition from the literature shows upstream sentence as none found', async () => {
  const sentence = 'No explicit definition matching the selection criteria was identified in the retrieved literature.';
  const { $ } = await view(succeeded({ definition_method: null, definition: sentence }));
  const html = $('run-body').innerHTML;
  assert.match(html, /<span class="pill neutral">None found<\/span><\/p><p class="muted">No explicit definition matching/);
  assert.doesNotMatch(html, /Pattern match|Selected by LLM/);
});

test('APA statuses other than found are worded without alarm', async () => {
  const { apaHtml } = await load('result.js');
  const unavailable = apaHtml({ term: 'x', status: 'unavailable', url: 'https://dictionary.apa.org/x', error: 'The page did not show an entry.' });
  assert.match(unavailable, /Could not be confirmed/);
  assert.match(unavailable, /<small>The page did not show an entry\.<\/small>/);
  assert.match(unavailable, /Look it up on dictionary\.apa\.org/);
  assert.match(apaHtml({ term: 'x', status: 'not_found' }), /No entry/);
  assert.match(apaHtml({ term: 'x', status: 'request_error', error: 'timed out' }), /Could not be checked[\s\S]*timed out/);
  assert.match(apaHtml(null), /was not checked/);
});

test('a result with only the required fields still renders', async () => {
  const minimal = { term: 'grit', apa_entry: { term: 'grit' }, definition: 'Grit is perseverance.', definition_method: 'pattern',
    generated_definition: null, evidence: [{ text: 'Grit is perseverance.' }] };
  const { $ } = await view(run({ status: 'succeeded', result: minimal, model: null, embedding_model: null }));
  const html = $('run-body').innerHTML;
  assert.match(html, /Grit is perseverance\./);
  assert.match(html, /<strong class="muted">Untitled<\/strong>/);
  assert.match(html, /Score —/);
  assert.doesNotMatch(html, /PubMed \d|PMC PMC|undefined|null|NaN/);
  assert.match(html, /<h2>What was read<\/h2><p class="muted">Not reported\.<\/p>/);
  assert.match(html, /<h2>Token usage<\/h2><p class="muted">Not reported\.<\/p>/);
  assert.equal($('run-download').hidden, false);
});

test('a succeeded run without a result says so and offers no download', async () => {
  const { $ } = await view(run({ status: 'succeeded', result: null }));
  assert.match($('run-body').innerHTML, /result is not available/);
  assert.equal($('run-download').hidden, true);
});

test('literature text and user input are escaped, and only http links are followed', async () => {
  const evil = '<script>alert(1)</script>';
  const { $ } = await view(succeeded({
    term: evil,
    definition: `${evil} is defined`,
    generated_definition: '<img src=x onerror=alert(2)>',
    apa_entry: { term: evil, status: 'found', url: 'javascript:alert(3)', entry_heading: '"><b>x</b>' },
    evidence: [{ text: evil, context_text: '<i>ctx</i>', title: '<b>t</b>', authors: ['<u>a</u>'], journal: '<s>j</s>', year: '<y>',
      pmid: '1"><x', pmcid: 'PMC1/../evil', source: '<em>s</em>', score: '<1>' }],
    counts: { '<k>': '<v>' },
    llm_usage: { model: '<m>', input_tokens: 1, output_tokens: 1, total_tokens: 2, selection: null, synthesis: { model: '<m2>' } },
    upstream: '<u>',
  }, { term: evil, model: '<model>' }));
  const html = $('run-body').innerHTML + $('run-summary').innerHTML;
  assert.doesNotMatch(html, /<(script|img|b|i|u|s|y|em|k|m|m2|model|x)[\s>]/);
  assert.doesNotMatch(html, /javascript:/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt; is defined/);
  assert.match(html, /href="https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/1%22%3E%3Cx\/"/);
  assert.match(html, /href="https:\/\/pmc\.ncbi\.nlm\.nih\.gov\/articles\/PMC1%2F..%2Fevil\/"/);
  // The title is set as text, never as markup.
  assert.equal($('run-title').textContent, evil);
});

test('the file name is made from the term the way upstream does', async () => {
  const { fileName } = await load('result.js');
  assert.equal(fileName('loneliness'), 'loneliness.json');
  assert.equal(fileName('  Social Anxiety '), 'social_anxiety.json');
  assert.equal(fileName('self-esteem'), 'self-esteem.json');
  assert.equal(fileName('Fear/Anxiety (state)'), 'fear_anxiety_state_.json');
  assert.equal(fileName('über   müde'), '_ber_m_de.json');
  assert.equal(fileName('snake_case'), 'snake_case.json');
});

test('Download JSON saves the result under that name', async () => {
  const { $, ctx } = await view(succeeded({ term: 'Social Anxiety' }));
  await fire($('run-download'), 'click');
  assert.equal(ctx.saved.length, 1);
  assert.equal(ctx.saved[0].name, 'social_anxiety.json');
  assert.deepEqual(JSON.parse(ctx.saved[0].text), result({ term: 'Social Anxiety' }));
});

test('saveJson hands the browser a JSON file to save', async () => {
  const dom = install();
  const { saveJson } = await load('run.js');
  const blobs = [];
  const links = [];
  const create = dom.document.createElement;
  dom.document.createElement = (tag) => { const el = create(tag); links.push(el); return el; };
  const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
  URL.createObjectURL = (blob) => { blobs.push(blob); return 'blob:result'; };
  URL.revokeObjectURL = () => {};
  try {
    saveJson('{"a":1}', 'loneliness.json');
    assert.equal(blobs[0].type, 'application/json');
    assert.equal(await blobs[0].text(), '{"a":1}');
    assert.equal(links[0].href, 'blob:result');
    assert.equal(links[0].download, 'loneliness.json');
    assert.equal(links[0].clicked, true);
    assert.deepEqual(dom.document.body.children, []);
  } finally {
    URL.createObjectURL = original.create;
    URL.revokeObjectURL = original.revoke;
  }
});

test('an unknown run says it does not exist', async () => {
  const { $, page } = await view(response({ detail: 'Definition run not found.' }, { status: 404 }), 'gone');
  assert.equal($('run-title').textContent, 'Run not found');
  assert.match($('run-msg').textContent, /This run does not exist/);
  assert.equal($('run-body').innerHTML, '');
  assert.equal(page.polling, false);
});

for (const [label, answer] of [
  ['a 404 from a route that is not mounted', () => response({ detail: 'Not Found' }, { status: 404 })],
  ['a 404 page from the wrong server', () => response('<html>Not found</html>', { status: 404 })],
]) {
  test(`${label} does not call the run missing`, async () => {
    const { $, page } = await view(answer(), 'run-1');
    assert.notEqual($('run-title').textContent, 'Run not found');
    assert.doesNotMatch($('run-msg').textContent, /does not exist/);
    assert.match($('run-msg').textContent, /definitions API was not found at .*\/psych_defgen\/api \(404\)/);
    assert.equal($('run-retry').hidden, false);
    assert.equal(page.polling, false);
  });
}

test('opening another run drops the late answer for the previous one', async () => {
  let release;
  const dom = install();
  globalThis.fetch = routeFetch({
    '/runs/slow': () => new Promise((resolve) => { release = () => resolve(response(run({ run_id: 'slow', term: 'old' }))); }),
    '/runs/fast': run({ run_id: 'fast', term: 'new', status: 'failed', error: 'x' }),
  });
  const { createRunView } = await load('run.js');
  const page = createRunView(context());
  page.show({ name: 'run', runId: 'slow' });
  await settle();
  await page.show({ name: 'run', runId: 'fast' });
  release();
  await settle();
  assert.equal(dom.$('run-title').textContent, 'new');
});

test('a first load that fails on a blip offers a retry and keeps trying until the run loads', async () => {
  let fail = true;
  const { $, page, timers, calls } = await view(() => (fail ? response('{"detail":"Bad gateway"}', { status: 502 }) : run({ status: 'running', stage: 'search' })));
  assert.equal(page.run, null);
  assert.equal($('run-msg').textContent, 'Could not load the run: Bad gateway');
  assert.equal($('run-retry').hidden, false);
  assert.equal(page.polling, true);
  fail = false;
  await timers.fire();
  assert.equal(calls.length, 2);
  assert.equal($('run-msg').textContent, '');
  assert.equal($('run-retry').hidden, true);
  assert.match($('run-body').innerHTML, /Searching PubMed/);
});

test('a first load refused for good is not polled, but Try again reads it again', async () => {
  let status = 403;
  const { $, page, timers, calls } = await view(() => (status ? response({ detail: 'No access.' }, { status }) : run({ status: 'failed', error: 'x' })));
  assert.equal($('run-msg').textContent, 'Could not load the run: No access.');
  assert.equal(page.polling, false);
  assert.equal(timers.count, 0);
  status = 0;
  await fire($('run-retry'), 'click');
  await settle();
  assert.equal(calls.length, 2);
  assert.match($('run-body').innerHTML, /The run failed/);
});

test('coming back to the tab reads a run again only while it can still change', async () => {
  let state = succeeded();
  const { $, page, calls } = await view(() => state);
  const html = $('run-body').innerHTML;
  await page.refresh();
  assert.equal(calls.length, 1, 'a succeeded run is not read again');
  assert.equal($('run-body').innerHTML, html);

  state = run({ status: 'running', stage: 'apa' });
  await page.show({ name: 'run', runId: 'run-1' });
  await page.refresh();
  assert.equal(calls.length, 3, 'a running run is');

  state = run({ status: 'failed', error: 'x' });
  await page.refresh();
  await page.refresh();
  assert.equal(calls.length, 4, 'and stops once it has failed');
});

test('a run not found or not loaded yet is handled on refresh', async () => {
  let answer = response({ detail: 'Definition run not found.' }, { status: 404 });
  const { page, calls } = await view(() => answer);
  await page.refresh();
  assert.equal(calls.length, 1, 'a missing run is not read again');

  answer = response('', { status: 503 });
  await page.show({ name: 'run', runId: 'other' });
  answer = run({ run_id: 'other', status: 'succeeded', result: result() });
  await page.refresh();
  assert.equal(calls.length, 3, 'one that never loaded is');
  assert.equal(page.run.status, 'succeeded');
});
