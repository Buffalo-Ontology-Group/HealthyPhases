// The API client: base URL, session, errors, and the contract's paths.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { install, load, response } = require('./harness.cjs');

test('the API base comes from the Nexus runtime config of this window, its parent or the top window', async () => {
  const dom = install();
  const { apiBase } = await load('api.js');
  assert.equal(apiBase(), '');
  dom.window.top = { __NEXUS_RUNTIME_CONFIG__: { apiUrl: 'https://top.example/' } };
  assert.equal(apiBase(), 'https://top.example');
  dom.window.parent = new Proxy({}, { get() { throw new Error('SecurityError'); } });
  assert.equal(apiBase(), 'https://top.example');
  dom.window.__NEXUS_RUNTIME_CONFIG__ = { apiUrl: 'https://self.example/' };
  assert.equal(apiBase(), 'https://self.example');
});

test('every call follows the contract with the session token and cookies', async () => {
  const dom = install({ storage: { 'nexus-auth': JSON.stringify({ state: { token: 'tok' } }) } });
  dom.window.__NEXUS_RUNTIME_CONFIG__ = { apiUrl: 'https://abi.example' };
  const api = await load('api.js');
  const seen = [];
  globalThis.fetch = async (url, options) => { seen.push({ url, options }); return response({}); };
  await api.getSettings();
  await api.startRun({ term: 'grit' });
  await api.listRuns(3);
  await api.getRun('a b/c');
  assert.deepEqual(seen.map((s) => `${s.options.method} ${s.url}`), [
    'GET https://abi.example/psych_defgen/api/settings',
    'POST https://abi.example/psych_defgen/api/runs',
    'GET https://abi.example/psych_defgen/api/runs?page=3&page_size=25',
    'GET https://abi.example/psych_defgen/api/runs/a%20b%2Fc',
  ]);
  for (const { options } of seen) {
    assert.equal(options.credentials, 'include');
    assert.equal(options.headers.Authorization, 'Bearer tok');
  }
  assert.equal(seen[1].options.headers['Content-Type'], 'application/json');
  assert.equal(seen[1].options.body, '{"term":"grit"}');
  assert.equal(seen[0].options.headers['Content-Type'], undefined);
});

test('without a session no Authorization header is sent', async () => {
  install({ storage: { 'nexus-auth': 'not json' } });
  const { authHeaders } = await load('api.js');
  assert.deepEqual(authHeaders(), {});
});

test('errors carry the detail, a readable list of fields, or a message for the status', async () => {
  install();
  const api = await load('api.js');
  globalThis.fetch = async () => response({ detail: 'NCBI_EMAIL is not set on this deployment.' }, { status: 409 });
  await assert.rejects(api.startRun({}), (error) => error.status === 409 && error.message === 'NCBI_EMAIL is not set on this deployment.');
  globalThis.fetch = async () => response({ detail: [{ loc: ['body', 'term'], msg: 'too long' }, { loc: ['query', 'page'], msg: 'bad' }] }, { status: 422 });
  await assert.rejects(api.listRuns(0), /^ApiError: term: too long; page: bad$/);
  globalThis.fetch = async () => response({}, { status: 401 });
  await assert.rejects(api.getSettings(), /session has expired/);
  globalThis.fetch = async () => response({}, { status: 418 });
  await assert.rejects(api.getSettings(), /The request failed \(418\)/);
});

test('an HTML answer is reported as the wrong server, never shown', async () => {
  install();
  const api = await load('api.js');
  globalThis.fetch = async () => response('<!doctype html><h1>404</h1>', { status: 404 });
  await assert.rejects(api.getRun('x'), (error) => /Expected JSON from \/psych_defgen\/api\/runs\/x but got 404/.test(error.message) && !error.message.includes('<'));
});

test('a network failure says the server could not be reached', async () => {
  install();
  const api = await load('api.js');
  globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(api.getSettings(), /Could not reach the server: Failed to fetch/);
});
