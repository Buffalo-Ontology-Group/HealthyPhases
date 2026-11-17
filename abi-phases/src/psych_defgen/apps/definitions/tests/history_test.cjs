// The history of runs: rows, pages, and polling while a shown run is in progress.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { context, fire, install, load, routeFetch, run, settle } = require('./harness.cjs');

const page = (runs, extra = {}) => ({ runs, total: runs.length, page: 1, page_count: runs.length ? 1 : 0, ...extra });

async function history(answer) {
  const dom = install();
  const calls = [];
  globalThis.fetch = routeFetch({ '/psych_defgen/api/runs': answer }, calls);
  const ctx = context();
  const { createHistory } = await load('history.js');
  const view = createHistory(ctx);
  await view.show();
  await settle();
  return { dom, ctx, view, calls, $: dom.$, timers: ctx.timers };
}

test('each run is a row with its term, status, stage, model, start and duration', async () => {
  const { $, calls } = await history(page([
    run({ run_id: 'r2', status: 'running', stage: 'full_text', started_at: '2026-11-16T10:00:05Z' }),
    run({ run_id: 'r1', status: 'succeeded', started_at: '2026-11-16T09:00:00Z', finished_at: '2026-11-16T09:03:12Z' }),
  ]));
  assert.match(calls[0].url, /\/psych_defgen\/api\/runs\?page=1&page_size=25$/);
  const html = $('history-body').innerHTML;
  assert.match(html, /href="#\/runs\/r2">loneliness<\/a>/);
  assert.match(html, /<span class="pill progress">Running<\/span><small>Reading full texts<\/small>/);
  assert.match(html, /<span class="pill ok">Succeeded<\/span>/);
  assert.match(html, /openai\/gpt-4\.1-mini/);
  assert.match(html, /3 min 12 s/);
  assert.match(html, /data-label="Duration"/);
});

test('an empty history says how to start', async () => {
  const { $ } = await history(page([]));
  assert.match($('history-body').innerHTML, /No runs yet/);
  assert.equal($('history-pager').hidden, true);
});

test('the term and model are escaped', async () => {
  const { $ } = await history(page([run({ term: '<img src=x onerror=alert(1)>', model: '"><b>m</b>', run_id: '"x' })]));
  const html = $('history-body').innerHTML;
  assert.doesNotMatch(html, /<img|<b>/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /href="#\/runs\/%22x"/);
});

test('pages move forward and back, and refresh reads the same page', async () => {
  const { $, calls } = await history((url) => {
    const n = Number(new URL(url, 'https://x').searchParams.get('page'));
    return { runs: [run({ run_id: `p${n}`, status: 'succeeded' })], total: 30, page: n, page_count: 2 };
  });
  assert.equal($('history-pager').hidden, false);
  assert.equal($('history-page').textContent, 'Page 1 of 2');
  assert.equal($('history-prev').disabled, true);
  assert.equal($('history-next').disabled, false);
  await fire($('history-next'), 'click');
  await settle();
  assert.match(calls.at(-1).url, /page=2&/);
  assert.equal($('history-page').textContent, 'Page 2 of 2');
  assert.equal($('history-next').disabled, true);
  await fire($('history-refresh'), 'click');
  await settle();
  assert.match(calls.at(-1).url, /page=2&/);
  await fire($('history-prev'), 'click');
  await settle();
  assert.match(calls.at(-1).url, /page=1&/);
});

test('polling runs every 5 s while a shown run is pending or running, and stops when none is', async () => {
  let runs = [run({ status: 'pending' }), run({ run_id: 'r0', status: 'succeeded' })];
  const { $, view, timers, calls } = await history(() => page(runs));
  assert.equal(view.polling, true);
  assert.deepEqual(timers.delays, [5000]);

  runs = [run({ status: 'running', stage: 'search' }), runs[1]];
  await timers.fire();
  assert.equal(calls.length, 2);
  assert.match($('history-body').innerHTML, /Searching PubMed/);
  assert.equal(timers.count, 1);

  runs = [run({ status: 'succeeded' }), runs[1]];
  await timers.fire();
  assert.equal(calls.length, 3);
  assert.equal(view.polling, false);
  assert.equal(timers.count, 0);
});

test('a finished history does not poll at all', async () => {
  const { view, timers } = await history(page([run({ status: 'failed', error: 'boom' })]));
  assert.equal(view.polling, false);
  assert.equal(timers.count, 0);
});

test('polling stops while the page is hidden and is not scheduled again until it is shown', async () => {
  const { dom, view, timers } = await history(page([run({ status: 'running', stage: 'ranking' })]));
  assert.equal(timers.count, 1);
  view.pause();
  assert.equal(timers.count, 0);
  dom.document.hidden = true;
  await view.refresh();
  assert.equal(timers.count, 0, 'a hidden page schedules nothing');
  dom.document.hidden = false;
  await view.refresh();
  assert.equal(timers.count, 1);
  view.hide();
  assert.equal(timers.count, 0);
});

test('a failed read is reported and polling goes on while work was in progress', async () => {
  let fail = false;
  const { $, timers } = await history(() => (fail ? Promise.reject(new TypeError('network down')) : page([run({ status: 'running', stage: 'apa' })])));
  fail = true;
  await timers.fire();
  assert.match($('history-msg').textContent, /Could not load the runs: Could not reach the server: network down/);
  assert.match($('history-body').innerHTML, /Checking the APA Dictionary/);
  assert.equal(timers.count, 1);
});
