// The new-definition form: settings, the notice for missing keys, validation and submitting.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { SETTINGS, context, fire, install, load, response, routeFetch, run, settle } = require('./harness.cjs');

async function form(routes = {}, { settings = SETTINGS } = {}) {
  const dom = install({ storage: { 'nexus-auth': JSON.stringify({ state: { token: 'session-token' } }) } });
  dom.window.__NEXUS_RUNTIME_CONFIG__ = { apiUrl: 'https://abi.example/' };
  const calls = [];
  globalThis.fetch = routeFetch({ '/psych_defgen/api/settings': settings, ...routes }, calls);
  const ctx = context();
  const { createForm } = await load('form.js');
  const view = createForm(ctx);
  await view.load();
  return { dom, ctx, view, calls, $: dom.$ };
}

const fill = ($, values) => {
  for (const [id, value] of Object.entries(values)) $(id).value = value;
};

test('the settings set the defaults, the limits and the models, and enable the form', async () => {
  const { $, calls } = await form();
  assert.equal(calls[0].url, 'https://abi.example/psych_defgen/api/settings');
  assert.equal($('def-max-results').value, '100');
  assert.equal($('def-max-results').min, '1');
  assert.equal($('def-max-results').max, '200');
  assert.equal($('def-top-k').value, '10');
  assert.equal($('def-top-k').max, '20');
  assert.deepEqual($('def-model').options.map((o) => o.value), ['openai/gpt-4.1-mini', 'openai/gpt-4.1']);
  assert.equal($('def-model').value, 'openai/gpt-4.1-mini');
  assert.match($('def-max-results-hint').textContent, /1–200/);
  assert.match($('def-embedding').textContent, /text-embedding-3-small/);
  assert.equal($('def-missing').hidden, true);
  assert.equal($('def-fieldset').disabled, false);
  assert.equal($('def-submit').disabled, false);
});

test('a missing key is named, and the form is disabled and sends nothing', async () => {
  const { $, view, calls } = await form({}, { settings: { ...SETTINGS, missing: ['NCBI_EMAIL'] } });
  assert.equal($('def-missing').hidden, false);
  assert.match($('def-missing-text').textContent, /^NCBI_EMAIL is not set on this deployment/);
  assert.equal($('def-fieldset').disabled, true);
  assert.equal($('def-submit').disabled, true);
  fill($, { 'def-term': 'loneliness' });
  assert.equal(await view.submit(), null);
  assert.equal(calls.length, 1);
});

test('several missing keys are all named', async () => {
  const { missingNotice } = await load('form.js');
  assert.match(missingNotice(['NCBI_EMAIL', 'OPENROUTER_API_KEY', 'OPENAI_API_KEY']), /^NCBI_EMAIL, OPENROUTER_API_KEY and OPENAI_API_KEY are not set on this deployment/);
  assert.equal(missingNotice([]), '');
});

test('validation explains each mistake before anything is sent', async () => {
  const { validate, TERM_MAX } = await load('form.js');
  const good = { term: ' loneliness ', max_results: '100', top_k: '10', model: 'openai/gpt-4.1-mini' };
  assert.deepEqual(validate(good, SETTINGS), { errors: {}, body: { term: 'loneliness', max_results: 100, top_k: 10, model: 'openai/gpt-4.1-mini' } });
  assert.match(validate({ ...good, term: '   ' }, SETTINGS).errors.term, /Enter a construct/);
  assert.equal(validate({ ...good, term: 'x'.repeat(TERM_MAX) }, SETTINGS).errors.term, undefined);
  assert.match(validate({ ...good, term: 'x'.repeat(TERM_MAX + 1) }, SETTINGS).errors.term, /200 characters or fewer/);
  for (const bad of ['0', '201', '2.5', '-1', '', 'ten']) {
    assert.match(validate({ ...good, max_results: bad }, SETTINGS).errors.max_results, /whole number from 1 to 200/, bad);
  }
  assert.equal(validate({ ...good, max_results: '200' }, SETTINGS).body.max_results, 200);
  assert.match(validate({ ...good, top_k: '21' }, SETTINGS).errors.top_k, /whole number from 1 to 20/);
  assert.match(validate({ ...good, model: 'someone/else' }, SETTINGS).errors.model, /Choose a model/);
  assert.equal(validate({ ...good, model: 'someone/else' }, SETTINGS).body, undefined);
});

test('an invalid form marks its fields, focuses the first and sends nothing', async () => {
  const { $, view, calls } = await form();
  fill($, { 'def-term': ' ', 'def-top-k': '50' });
  assert.equal(await view.submit(), null);
  assert.equal(calls.length, 1);
  assert.equal($('def-term').getAttribute('aria-invalid'), 'true');
  assert.equal($('def-top-k').getAttribute('aria-invalid'), 'true');
  assert.equal($('def-max-results').getAttribute('aria-invalid'), null);
  assert.match($('def-term-error').textContent, /Enter a construct/);
  assert.match($('def-top-k-error').textContent, /1 to 20/);
  assert.equal($('def-term').focused, true);
});

test('a valid form starts the run with the session and opens it', async () => {
  const { $, ctx, calls } = await form({ 'POST /psych_defgen/api/runs': response(run({ run_id: 'abc/1' }), { status: 201 }) });
  fill($, { 'def-term': '  loneliness ', 'def-max-results': '50', 'def-top-k': '5' });
  $('def-model').value = 'openai/gpt-4.1';
  await fire($('def-form'), 'submit');
  await settle();
  const sent = calls.at(-1);
  assert.equal(sent.method, 'POST');
  assert.equal(sent.url, 'https://abi.example/psych_defgen/api/runs');
  assert.deepEqual(sent.body, { term: 'loneliness', max_results: 50, top_k: 5, model: 'openai/gpt-4.1' });
  assert.equal(sent.headers.Authorization, 'Bearer session-token');
  assert.equal(sent.credentials, 'include');
  assert.deepEqual(ctx.navigated, ['#/runs/abc%2F1']);
  assert.equal($('def-term').value, '');
  assert.equal($('def-submit').disabled, false);
});

test('the API refusing the run shows its reason and keeps what was typed', async () => {
  const { $, view, ctx } = await form({ 'POST /psych_defgen/api/runs': response({ detail: [{ loc: ['body', 'top_k'], msg: 'must be at most 20' }] }, { status: 422 }) });
  fill($, { 'def-term': 'loneliness' });
  await view.submit();
  assert.equal($('def-msg').textContent, 'top_k: must be at most 20');
  assert.equal($('def-msg').className, 'msg bad');
  assert.equal($('def-term').value, 'loneliness');
  assert.deepEqual(ctx.navigated, []);
});

test('a key removed since the settings were read is named after the 409', async () => {
  let settings = SETTINGS;
  const { $, view } = await form({
    '/psych_defgen/api/settings': () => settings,
    'POST /psych_defgen/api/runs': () => {
      settings = { ...SETTINGS, missing: ['OPENAI_API_KEY'] };
      return response({ detail: 'OPENAI_API_KEY is not set on this deployment.' }, { status: 409 });
    },
  });
  fill($, { 'def-term': 'loneliness' });
  await view.submit();
  assert.match($('def-missing-text').textContent, /^OPENAI_API_KEY is not set/);
  assert.equal($('def-fieldset').disabled, true);
  assert.equal($('def-submit').disabled, true);
});

test('settings that fail to load say so, and can be read again', async () => {
  let fail = true;
  const { $, view } = await form({ '/psych_defgen/api/settings': () => (fail ? response('<html>', { status: 502 }) : SETTINGS) });
  assert.match($('def-msg').textContent, /Could not load the settings: Expected JSON/);
  assert.equal($('def-retry').hidden, false);
  assert.equal($('def-fieldset').disabled, true);
  fail = false;
  await fire($('def-retry'), 'click');
  await settle();
  assert.equal($('def-retry').hidden, true);
  assert.equal($('def-fieldset').disabled, false);
  assert.equal(view.settings.default_model, 'openai/gpt-4.1-mini');
});
