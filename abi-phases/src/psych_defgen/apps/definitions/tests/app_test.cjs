// The whole app against a fake API that follows the contract: start a run,
// watch it finish, browse the history, open a result and download its JSON.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { SETTINGS, context, fire, install, load, response, result, run, settle } = require('./harness.cjs');

// A fake /psych_defgen/api: each read of a run moves it one step on.
function fakeApi({ settings = SETTINGS } = {}) {
  const runs = [];
  const steps = [
    { status: 'running', stage: 'search', started_at: '2026-11-16T10:00:05Z' },
    { status: 'running', stage: 'synthesis' },
    { status: 'succeeded', stage: null, finished_at: '2026-11-16T10:03:05Z' },
  ];
  const progress = new Map();
  const calls = [];
  const fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    const { pathname, searchParams } = new URL(url, 'https://abi.example');
    calls.push(`${method} ${pathname}${searchParams.size ? `?${searchParams}` : ''}`);
    if (pathname === '/psych_defgen/api/settings') return response(settings);
    if (pathname === '/psych_defgen/api/runs' && method === 'POST') {
      const body = JSON.parse(options.body);
      const created = run({ run_id: `run-${runs.length + 1}`, term: body.term.trim(), max_results: body.max_results, top_k: body.top_k, model: body.model });
      runs.unshift(created);
      progress.set(created.run_id, 0);
      return response(created, { status: 201 });
    }
    if (pathname === '/psych_defgen/api/runs') {
      const page = Number(searchParams.get('page'));
      const size = Number(searchParams.get('page_size'));
      const shown = runs.slice((page - 1) * size, page * size).map(({ result: _r, ...rest }) => ({ ...rest, result: null }));
      return response({ runs: shown, total: runs.length, page, page_count: Math.ceil(runs.length / size) });
    }
    const id = decodeURIComponent(pathname.split('/').at(-1));
    const found = runs.find((r) => r.run_id === id);
    if (!found) return response({ detail: 'Run not found.' }, { status: 404 });
    return response(found);
  };
  // The worker moves the run on.
  const advance = (id) => {
    const found = runs.find((r) => r.run_id === id);
    const step = progress.get(id);
    Object.assign(found, steps[step]);
    if (found.status === 'succeeded') found.result = result({ term: found.term });
    progress.set(id, step + 1);
  };
  return { fetch, calls, runs, advance };
}

async function boot(api, options = {}) {
  const dom = install(options);
  globalThis.fetch = api.fetch;
  const ctx = context();
  const { boot: start } = await load('app.js');
  const app = start({ timers: ctx.timers, save: ctx.save });
  await settle();
  return { dom, ctx, app, $: dom.$, timers: ctx.timers };
}

const go = async (dom, hash) => { dom.location.hash = hash; dom.hashchange(); await settle(); };

test('a user starts a run, watches it finish, finds it in the history and downloads its JSON', async () => {
  const api = fakeApi();
  const { dom, ctx, app, $, timers } = await boot(api);
  assert.equal($('view-home').hidden, false);
  assert.equal($('view-run').hidden, true);
  assert.match($('history-body').innerHTML, /No runs yet/);
  assert.equal(timers.count, 0, 'nothing to follow yet');

  $('def-term').value = 'Social Anxiety';
  await fire($('def-form'), 'submit');
  await settle();
  assert.equal(dom.location.hash, '#/runs/run-1');
  dom.hashchange();
  await settle();
  assert.equal($('view-run').hidden, false);
  assert.equal($('view-home').hidden, true);
  assert.match($('run-body').innerHTML, /Waiting to start/);
  assert.equal(timers.count, 1);

  api.advance('run-1');
  await timers.fire();
  assert.match($('run-body').innerHTML, /Searching PubMed/);
  api.advance('run-1');
  await timers.fire();
  assert.match($('run-body').innerHTML, /Writing the AI-synthesized definition/);
  api.advance('run-1');
  await timers.fire();
  assert.equal(timers.count, 0, 'polling stops once the run has finished');
  assert.match($('run-body').innerHTML, /Definition from the literature/);
  assert.match($('run-summary').innerHTML, /3 min 0 s/);

  await fire($('run-download'), 'click');
  assert.equal(ctx.saved[0].name, 'social_anxiety.json');
  assert.equal(JSON.parse(ctx.saved[0].text).term, 'Social Anxiety');

  await go(dom, '#/');
  assert.equal($('view-home').hidden, false);
  assert.match($('history-body').innerHTML, /href="#\/runs\/run-1" data-run-id="run-1">Social Anxiety<\/a>/);
  assert.match($('history-body').innerHTML, /Succeeded/);
  assert.equal(timers.count, 0);

  await go(dom, '#/runs/run-1');
  assert.match($('run-body').innerHTML, /Evidence \(2\)/);
  assert.equal(app.active, 'run');
  assert.ok(api.calls.includes('POST /psych_defgen/api/runs'));
});

test('only the shown view polls, and nothing polls while the page is hidden', async () => {
  const api = fakeApi();
  const { dom, $, timers } = await boot(api);
  $('def-term').value = 'grit';
  await fire($('def-form'), 'submit');
  await settle();
  // Still on the home page: the history follows the pending run.
  await go(dom, '#/');
  assert.match($('history-body').innerHTML, /Pending/);
  assert.equal(timers.count, 1);

  dom.visibility(true);
  assert.equal(timers.count, 0, 'hidden: stopped');
  const before = api.calls.length;
  dom.visibility(false);
  await settle();
  assert.equal(api.calls.length, before + 1, 'visible again: read at once');
  assert.equal(timers.count, 1);

  await go(dom, '#/runs/run-1');
  assert.equal(timers.count, 1, 'the history stopped and the run page took over');
  await go(dom, '#/');
  assert.equal(timers.count, 1);
});

test('the app reopens on the last route after Nexus reloads the frame', async () => {
  const api = fakeApi();
  api.runs.push(run({ run_id: 'old', term: 'grit', status: 'failed', error: 'NCBI did not answer.' }));
  const session = { 'healthyphases.definitions.route': '#/runs/old' };
  const { $ } = await boot(api, { session });
  assert.equal($('view-run').hidden, false);
  assert.equal($('run-title').textContent, 'grit');
  assert.match($('run-body').innerHTML, /NCBI did not answer\./);
});

test('a deployment missing a key names it and offers no form, but still shows the history', async () => {
  const api = fakeApi({ settings: { ...SETTINGS, missing: ['NCBI_EMAIL'] } });
  api.runs.push(run({ run_id: 'old', term: 'grit', status: 'succeeded' }));
  const { $ } = await boot(api);
  assert.equal($('def-missing').hidden, false);
  assert.match($('def-missing-text').textContent, /NCBI_EMAIL is not set on this deployment/);
  assert.equal($('def-fieldset').disabled, true);
  assert.equal($('def-submit').disabled, true);
  assert.match($('history-body').innerHTML, /grit/);
});

test('coming back to the tab reads the settings again while a key is missing', async () => {
  const settings = { ...SETTINGS, missing: ['NCBI_EMAIL'] };
  const api = fakeApi({ settings });
  const { dom, $ } = await boot(api);
  assert.equal($('def-fieldset').disabled, true);
  settings.missing = [];
  dom.visibility(true);
  dom.visibility(false);
  await settle();
  assert.equal($('def-missing').hidden, true);
  assert.equal($('def-fieldset').disabled, false);
  const reads = api.calls.filter((c) => c.endsWith('/settings')).length;
  dom.visibility(true);
  dom.visibility(false);
  await settle();
  assert.equal(api.calls.filter((c) => c.endsWith('/settings')).length, reads, 'not once they are fine');
});
