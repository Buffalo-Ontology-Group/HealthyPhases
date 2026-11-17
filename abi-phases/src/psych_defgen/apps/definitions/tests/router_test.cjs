// Routes, and the last one restored after Nexus reloads the frame without its hash.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { install, load } = require('./harness.cjs');

test('#/ is the home page and #/runs/<run_id> one run', async () => {
  const { parse, runHref } = await load('router.js');
  assert.deepEqual(parse(''), { name: 'home' });
  assert.deepEqual(parse('#/'), { name: 'home' });
  assert.deepEqual(parse('#/runs'), { name: 'home' });
  assert.deepEqual(parse('#/elsewhere/x'), { name: 'home' });
  assert.deepEqual(parse('#/runs/abc-123'), { name: 'run', runId: 'abc-123' });
  assert.deepEqual(parse('#/runs/a%2Fb?x=1'), { name: 'run', runId: 'a/b' });
  assert.deepEqual(parse('#/runs/%E0%A4%A'), { name: 'run', runId: '%E0%A4%A' });
  assert.equal(runHref('a/b'), '#/runs/a%2Fb');
  assert.deepEqual(parse(runHref('a/b c')), { name: 'run', runId: 'a/b c' });
});

test('the last route is remembered and restored when the frame opens without a hash', async () => {
  const session = {};
  let dom = install({ hash: '#/runs/r1', session });
  const { createRouter } = await load('router.js');
  const seen = [];
  createRouter((route) => seen.push(route)).start();
  assert.equal(session['healthyphases.definitions.route'], '#/runs/r1');

  dom = install({ hash: '', session });
  const restored = [];
  createRouter((route) => restored.push(route)).start();
  assert.equal(dom.location.hash, '#/runs/r1');
  assert.deepEqual(restored, [{ name: 'run', runId: 'r1' }]);
});

test('following a link changes the route, and navigating to the current one re-renders it', async () => {
  const dom = install({ hash: '#/' });
  const { createRouter } = await load('router.js');
  const seen = [];
  const router = createRouter((route) => seen.push(route.name));
  router.start();
  router.navigate('#/runs/r2');
  assert.equal(dom.location.hash, '#/runs/r2');
  dom.hashchange();
  router.navigate('#/runs/r2');
  assert.deepEqual(seen, ['home', 'run', 'run']);
  assert.equal(dom.session['healthyphases.definitions.route'], '#/runs/r2');
});
