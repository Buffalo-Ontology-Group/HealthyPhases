// Owners in the app: Mine / Everyone's filters, who owns each row, and the
// controls a user cannot use on what someone else owns or on shared prompts.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { context, fire, install, load, routeFetch, settle } = require('./harness.cjs');

const ADA = { owner_id: 'user-ada', owner_name: 'Ada Example', owned_by_you: true, shared: false, can_change: true, read_only_reason: null };
const BEA = {
  owner_id: 'user-bea', owner_name: 'Bea Example', owned_by_you: false, shared: false, can_change: false,
  read_only_reason: 'The prompt “Bea causes” belongs to Bea Example, and only its owner saves new versions of it. Choose another name, or use Duplicate prompt to get your own copy.',
};
const SHARED = {
  owner_id: null, owner_name: null, owned_by_you: false, shared: true, can_change: false,
  read_only_reason: '“effects” is a shared prompt, and shared prompts are read-only. Use Duplicate prompt to get your own copy, then change that.',
};

// ---- Prompts ----

const PROMPTS = { prompts: [
  { ...ADA, prompt_id: 'ada-claims-aaaaaaaa', name: 'Ada claims', template: 'A {chunk_text}', output_key: 'results' },
  { ...BEA, prompt_id: 'bea-causes-bbbbbbbb', name: 'Bea causes', template: 'B {chunk_text}', output_key: 'causes' },
  { ...SHARED, prompt_id: 'effects-cccccccc', name: 'effects', template: 'C {chunk_text}', output_key: 'effects', declared: true },
  { ...SHARED, prompt_id: 'effects-copy-dddddddd', name: 'effects copy', template: 'D {chunk_text}', output_key: 'effects' },
] };

async function prompts(list = PROMPTS, routes = {}) {
  const dom = install();
  const { href, parse } = await load('router.js');
  const runs = { selectPrompts() {} };
  const ctx = context({ href, sections: { runs } });
  const calls = [];
  globalThis.fetch = routeFetch({
    '/phases_v2/api/prompts': list,
    '/phases_v2/api/prompts/versions': { name: '', versions: [] },
    '/phases_v2/api/models': { models: [] },
    '/phases_v2/api/chunkers': { chunkers: [] },
    '/phases_v2/api/papers': { papers: [], total: 0 },
    ...routes,
  }, calls);
  const { create } = await load('sections/prompts.js');
  const section = create(dom.$('section-prompts'), ctx);
  return { dom, section, parse, calls, $: dom.$, a: section.actions };
}

const listed = ($) => $('pr-list').children.map((b) => b.innerHTML);

test('the prompt library shows your own prompts first, and everyone’s with their owner on request', async () => {
  const { $, section, parse, a } = await prompts();
  await section.show(parse('#/prompts'));
  await settle(10);

  assert.equal($('pr-owner').value, 'mine');
  assert.deepEqual(listed($), ['Ada claims<small>results · You</small>']);
  // The first prompt shown opens by default.
  assert.equal($('pr-name').value, 'Ada claims');

  a.chooseOwner('all');
  assert.deepEqual(listed($), [
    'Ada claims<small>results · You</small>',
    'Bea causes<small>causes · Bea Example</small>',
    'effects<small>effects · Shared</small>',
    'effects copy<small>effects · Shared</small>',
  ]);
  $('pr-owner').value = 'mine';
  await fire($('pr-owner'), 'change');
  assert.equal(listed($).length, 1);
});

test('with none of your own, the library points to creating or duplicating one', async () => {
  const others = { prompts: PROMPTS.prompts.filter((p) => !p.owned_by_you) };
  const { $, section, parse } = await prompts(others);
  await section.show(parse('#/prompts'));
  await settle(10);

  assert.match($('pr-list').innerHTML, /You have no prompts of your own yet\. Write one with New prompt, or choose Everyone’s, open a prompt and use Duplicate prompt\./);
  assert.match($('pr-list').innerHTML, /href="#\/help\/prompts\/duplicate"/);
  assert.equal($('pr-title').textContent, 'New prompt');
});

test('a shared or someone else’s prompt cannot take a new version, and says why', async () => {
  const { $, section, parse, a, calls } = await prompts();
  for (const [id, reason] of [['effects-cccccccc', /shared prompt/], ['bea-causes-bbbbbbbb', /belongs to Bea Example/]]) {
    await section.show(parse(`#/prompts/${id}`));
    await settle(10);
    assert.equal($('pr-save').disabled, true, id);
    assert.equal($('pr-try-save').disabled, true, id);
    assert.match($('pr-save').title, reason);
    assert.match($('pr-owner-line').textContent, /^Read-only: /);
    assert.equal($('pr-owner-line').className, 'msg warn');
    assert.equal($('pr-duplicate').className, 'primary');
    assert.equal($('pr-duplicate').disabled, false);
    // Try it and Use in new run stay open to everyone.
    assert.equal($('pr-use').disabled, false);
  }
  await assert.rejects(a.save(), /belongs to Bea Example/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0, 'nothing is sent');

  await section.show(parse('#/prompts/ada-claims-aaaaaaaa'));
  await settle(10);
  assert.equal($('pr-save').disabled, false);
  assert.match($('pr-owner-line').textContent, /You own this prompt/);
});

test('typing a name someone else has disables saving; a new name is yours to save', async () => {
  const { $, section, parse } = await prompts();
  await section.show(parse('#/prompts/new'));
  await settle(10);
  $('pr-name').value = 'BEA CAUSES';
  await fire($('pr-form'), 'input');
  assert.equal($('pr-save').disabled, true);
  assert.match($('pr-owner-line').textContent, /belongs to Bea Example/);

  $('pr-name').value = 'Fresh name';
  await fire($('pr-form'), 'input');
  assert.equal($('pr-save').disabled, false);
  assert.match($('pr-owner-line').textContent, /Saving makes this prompt yours/);
});

test('Duplicate prompt copies a read-only prompt under a free name that can be saved', async () => {
  const saved = { ...ADA, prompt_id: 'effects-copy-2-eeeeeeee', name: 'effects copy 2', template: 'C {chunk_text}', output_key: 'effects' };
  const { $, section, parse, a, calls } = await prompts(PROMPTS, { 'POST /phases_v2/api/prompts': saved });
  await section.show(parse('#/prompts/effects-cccccccc'));
  await settle(10);

  a.duplicate();
  // "effects copy" is taken, so the copy is numbered.
  assert.equal($('pr-name').value, 'effects copy 2');
  assert.equal($('pr-template').value, 'C {chunk_text}');
  assert.equal($('pr-save').disabled, false);

  await a.save();
  const post = calls.find((c) => c.method === 'POST');
  assert.deepEqual(post.body, { name: 'effects copy 2', template: 'C {chunk_text}', output_key: 'effects' });
});

// ---- Collections ----

const COL_ADA = { ...ADA, collection_id: 'c-ada', name: 'Ada set', description: '', member_count: 1, paper_count: 1, updated_at: null, locations: [] };
const COL_BEA = {
  ...BEA, collection_id: 'c-bea', name: 'Bea set', description: 'Hers', member_count: 1, paper_count: 1, updated_at: null, locations: [],
  read_only_reason: 'The collection “Bea set” belongs to Bea Example, and only its owner changes it. You can still run on it, see its results and search within it. To change what it holds, create a collection of your own.',
};
const COL_SHARED = { ...SHARED, can_change: true, read_only_reason: null, collection_id: 'c-old', name: 'Old folders', description: '', member_count: 1, paper_count: 1, updated_at: null, locations: [] };
const detail = (c) => ({
  ...c, members: [{ member_id: 'm1', kind: 'folder', prefix: 'phases_v2/a', label: 'Folder A', paper_count: 1, error: null }],
  papers: [], paper_count: c.paper_count, status_counts: {}, unsupported: 0, truncated: false,
});

// The papers root first, as the server lists it, then folders and upload folders.
const LOCATIONS = [
  { prefix: 'phases_v2', name: 'phases_v2 (all)' },
  { prefix: 'phases_v2/a', name: 'Folder A' },
  { prefix: 'phases_v2/collections/c-ada', name: 'Ada set uploads' },
  { prefix: 'phases_v2/collections/c-bea', name: 'Bea set uploads' },
  { prefix: 'phases_v2/collections/c-old', name: 'Old folders uploads' },
  { prefix: 'phases_v2/collections/c-archived', name: 'Archived uploads' },
];

async function collections(list = [COL_ADA, COL_BEA, COL_SHARED]) {
  const dom = install();
  const { href, parse } = await load('router.js');
  const { confirmAction } = await load('ui.js');
  const runs = { useCollection() {} };
  const ctx = context({ href, confirm: confirmAction, sections: { runs } });
  const calls = [];
  globalThis.fetch = routeFetch({
    '/phases_v2/api/locations': { locations: LOCATIONS },
    '/phases_v2/api/collections': { collections: list },
    '/phases_v2/api/collections/c-ada': detail(COL_ADA),
    '/phases_v2/api/collections/c-bea': detail(COL_BEA),
    '/phases_v2/api/collections/c-old': detail(COL_SHARED),
    '/phases_v2/api/sources/pubmed': { available: true, queries: [{ query_id: 'q1', query: 'solitude', published_paper_count: 2 }] },
  }, calls);
  const { create, CHANGE_CONTROLS } = await load('sections/collections.js');
  const section = create(dom.$('section-collections'), ctx);
  return { dom, section, parse, calls, CHANGE_CONTROLS, $: dom.$, a: section.actions };
}

test('Collections lists yours first, and everyone’s with their owner on request', async () => {
  const { $, section, parse, a } = await collections();
  await section.show(parse('#/collections'));
  await settle(10);

  assert.equal($('col-owner').value, 'mine');
  assert.equal($('col-list-title').textContent, 'Your collections');
  assert.deepEqual($('col-list').children.map((b) => b.innerHTML), ['Ada set<small>1 member · 1 paper · You</small>']);
  assert.equal($('col-title').textContent, 'Ada set');

  a.chooseOwner('all');
  assert.equal($('col-list-title').textContent, 'Everyone’s collections');
  assert.deepEqual($('col-list').children.map((b) => b.innerHTML), [
    'Ada set<small>1 member · 1 paper · You</small>',
    'Bea set<small>1 member · 1 paper · Bea Example</small>',
    'Old folders<small>1 member · 1 paper · Shared</small>',
  ]);
});

test('with none of your own, Collections points to creating one', async () => {
  const { $, section, parse } = await collections([COL_BEA]);
  await section.show(parse('#/collections'));
  await settle(10);

  assert.match($('col-list').innerHTML, /You have no collections of your own yet\. Create one with New collection/);
  assert.match($('col-list').innerHTML, /href="#\/help\/collections\/create"/);
  assert.equal($('col-title').textContent, 'New collection');
});

test('someone else’s collection opens read-only, with running and searching still open', async () => {
  const { $, section, parse, a, calls, CHANGE_CONTROLS } = await collections();
  await section.show(parse('#/collections/c-bea'));
  await settle(10);

  for (const id of CHANGE_CONTROLS) assert.equal($(id).disabled, true, id);
  assert.equal($('col-archive').hidden, true);
  assert.match($('col-owner-line').textContent, /^Read-only: The collection “Bea set” belongs to Bea Example/);
  assert.equal($('col-owner-line').className, 'msg warn');
  assert.match($('col-add-hint').textContent, /Only this collection’s owner adds papers/);
  // No Remove button beside its members.
  assert.deepEqual($('col-members').children.map((row) => row.lastElementChild.children.length), [0]);
  assert.equal($('col-run').disabled, false);
  assert.equal($('col-search').hidden, false);
  assert.equal($('col-results').hidden, false);

  await assert.rejects(a.addFolder(), /belongs to Bea Example/);
  await assert.rejects(a.archive(), /belongs to Bea Example/);
  assert.equal(calls.filter((c) => c.method !== 'GET').length, 0, 'nothing is sent');
});

test('your own and shared collections can be changed', async () => {
  const { $, section, parse, CHANGE_CONTROLS } = await collections();
  for (const [id, line] of [['c-ada', /You own this collection/], ['c-old', /Shared collection: everyone can change it/]]) {
    await section.show(parse(`#/collections/${id}`));
    await settle(10);
    for (const control of CHANGE_CONTROLS) assert.equal($(control).disabled, false, `${id} ${control}`);
    assert.equal($('col-archive').hidden, false);
    assert.match($('col-owner-line').textContent, line);
    assert.equal($('col-members').children[0].lastElementChild.children.length, 1, 'Remove');
  }
});

test('Add to collection offers only collections you can change', async () => {
  const { $, section } = await collections();
  section.addToCollection([{ paper: { pmid: '7' } }], 'a paper');
  await settle(5);

  assert.deepEqual($('col-pick-select').options.map((o) => o.textContent), ['Ada set', 'Old folders', 'New collection...']);
});

// ---- Runs ----

const REQUESTS = {
  mine: [{ request_id: 'r-ada', status: 'pending', requested_at: '2026-11-01T10:00:00Z', locations: ['phases_v2/a'], prompt_ids: [], model_id: 'model', requested_by: 'user-ada', requested_by_name: 'Ada Example', requested_by_you: true }],
  all: [
    { request_id: 'r-ada', status: 'pending', requested_at: '2026-11-01T10:00:00Z', locations: ['phases_v2/a'], prompt_ids: [], model_id: 'model', requested_by: 'user-ada', requested_by_name: 'Ada Example', requested_by_you: true },
    { request_id: 'r-bea', status: 'succeeded', requested_at: '2026-10-01T10:00:00Z', locations: ['phases_v2/a'], prompt_ids: [], model_id: 'model', requested_by: 'user-bea', requested_by_name: 'Bea Example', requested_by_you: false },
    { request_id: 'r-old', status: 'failed', requested_at: '2026-09-01T10:00:00Z', locations: ['phases_v2/a'], prompt_ids: [], model_id: 'model', requested_by: null, requested_by_name: null, requested_by_you: false },
  ],
};
const PIPELINES = { pipelines: [
  { ...ADA, pipeline_id: 'p-ada', name: 'Ada weekly', created_at: '2026-09-01T10:00:00Z', inputs: { locations: ['phases_v2/a'], prompt_ids: [], model_id: 'model', chunker_id: 'chunker' } },
  { ...BEA, pipeline_id: 'p-bea', name: 'Bea daily', created_at: '2026-09-02T10:00:00Z', inputs: { locations: ['phases_v2/a'], prompt_ids: [], model_id: 'model', chunker_id: 'chunker' } },
] };

async function runs(pipelines = PIPELINES) {
  const dom = install();
  const { href, parse } = await load('router.js');
  const ctx = context({ href });
  const calls = [];
  globalThis.fetch = routeFetch({
    '/phases_v2/api/locations': { locations: [{ prefix: 'phases_v2/a', name: 'Folder A' }] },
    '/phases_v2/api/chunkers': { chunkers: [{ chunker_id: 'chunker', name: 'Pages', version: 2 }] },
    '/phases_v2/api/prompts': { prompts: [] },
    '/phases_v2/api/models': { models: [{ model_id: 'model', display_name: 'Model One' }] },
    '/phases_v2/api/collections': { collections: [COL_ADA, COL_BEA] },
    '/phases_v2/api/pipelines': pipelines,
    '/phases_v2/api/chunker-warning': { warning: null },
    '/phases_v2/api/documents': { documents: [], total: 0 },
    '/phases_v2/api/sources/pubmed': { available: true, queries: [] },
    '/phases_v2/api/requests': (url) => ({ requests: REQUESTS[new URL(url, 'https://x').searchParams.get('owner')] }),
  }, calls);
  const { create } = await load('sections/runs.js');
  const section = create(dom.$('section-runs'), ctx);
  await section.actions.loadChoices();
  await settle();
  return { dom, section, parse, calls, $: dom.$, a: section.actions };
}

const requestUrls = (calls) => calls.filter((c) => /\/requests\?/.test(c.url)).map((c) => c.url.replace(/^.*\/requests/, ''));

test('Requests asks for your runs first, and everyone’s on request, with who requested each', async () => {
  const { $, a, calls } = await runs();
  assert.equal($('run-owner').value, 'mine');
  await a.loadRequests();
  assert.deepEqual(requestUrls(calls), ['?limit=100&owner=mine']);
  assert.equal($('run-requests').children.length, 1);
  assert.match($('run-requests').children[0].innerHTML, /<td>You<\/td>$/);

  await a.chooseOwner('all');
  assert.deepEqual(requestUrls(calls).at(-1), '?limit=100&owner=all');
  assert.deepEqual($('run-requests').children.map((row) => row.innerHTML.match(/<td>([^<]*)<\/td>$/)[1]), ['You', 'Bea Example', '—']);
});

test('with no runs of your own, Requests points to New run', async () => {
  const { $, a } = await runs();
  REQUESTS.mine_saved = REQUESTS.mine;
  REQUESTS.mine = [];
  try {
    await a.loadRequests();
  } finally {
    REQUESTS.mine = REQUESTS.mine_saved;
    delete REQUESTS.mine_saved;
  }
  assert.match($('run-requests').innerHTML, /You have not requested any runs yet\. Start one from New run, or show Everyone’s\./);
});

test('a run’s page says who requested it', async () => {
  const { $, section, parse, dom } = await runs();
  globalThis.fetch = routeFetch({
    '/phases_v2/api/requests/r-bea': REQUESTS.all[1],
    '/phases_v2/api/requests': { requests: REQUESTS.mine },
    '/phases_v2/api/sources/pubmed': { available: true, queries: [] },
  });
  await section.show(parse('#/runs/r-bea'));
  await settle(10);
  assert.match(dom.$('run-detail-content').innerHTML, /<dt>Requested by<\/dt><dd>Bea Example<\/dd>/);
  assert.equal($('run-detail').hidden, false);
});

test('saved configurations show yours first, everyone’s with their owner, and anyone’s can be loaded', async () => {
  const { $, a } = await runs();
  assert.equal($('run-saved-owner').value, 'mine');
  assert.deepEqual($('run-saved').options.map((o) => o.value), ['p-ada']);
  assert.match($('run-saved').options[0].textContent, /^Ada weekly · You \(/);

  $('run-saved-owner').value = 'all';
  await fire($('run-saved-owner'), 'change');
  assert.deepEqual($('run-saved').options.map((o) => o.value), ['p-ada', 'p-bea']);
  assert.match($('run-saved').options[1].textContent, /^Bea daily · Bea Example \(/);
  assert.equal($('run-load-saved').disabled, false);
  assert.equal($('run-run-saved').disabled, false);
});

test('with none saved by you, saved configurations say so and cannot be loaded', async () => {
  const { $ } = await runs({ pipelines: [PIPELINES.pipelines[1]] });
  assert.match($('run-saved').options[0].textContent, /You have no saved configurations yet/);
  assert.equal($('run-load-saved').disabled, true);
  assert.equal($('run-run-saved').disabled, true);
});

test('a collection someone else owns can be chosen for a run, labelled with its owner', async () => {
  const { $ } = await runs();
  assert.deepEqual($('run-collection').options.map((o) => o.textContent), ['Ada set · 1 member', 'Bea set · 1 member · Bea Example']);
});

test('Upload PDFs to a folder leaves out the upload folders of collections you cannot change', async () => {
  const { $, section, parse } = await collections();
  await section.show(parse('#/collections'));
  await settle(10);

  assert.deepEqual($('col-folder-upload-location').options.map((o) => o.textContent), [
    'phases_v2 (all)', 'Folder A', 'Ada set uploads', 'Old folders uploads',
  ]);
  // Adding a folder to your own collection reads it, so every folder is offered there.
  assert.equal($('col-folder').options.length, LOCATIONS.length);
});

test('a copy of a long prompt name still fits in 100 characters', async () => {
  install();
  const { copyName, MAX_NAME } = await load('sections/prompts.js');
  const long = 'L'.repeat(100);
  const taken = [{ name: long }, { name: `${'L'.repeat(95)} copy` }];
  const copy = copyName(long, taken);
  assert.equal(copy, `${'L'.repeat(93)} copy 2`);
  assert.equal(copy.length, MAX_NAME);
  assert.equal(copyName('Short', []), 'Short copy');
  assert.equal(copyName('Short', [{ name: 'short COPY' }]), 'Short copy 2');
});

test('an older requests answer never replaces a newer one', async () => {
  const { $, a, calls } = await runs();
  const answers = [];
  globalThis.fetch = routeFetch({
    '/phases_v2/api/requests': (url) => new Promise((resolve) => answers.push({ url, resolve })),
    '/phases_v2/api/sources/pubmed': { available: true, queries: [] },
  }, calls);

  const slow = a.loadRequests();
  await settle();
  // The 10-second poll does not stack a second load behind the first.
  a.loadRequests();
  await settle();
  assert.equal(answers.length, 1);
  const fresh = a.chooseOwner('all');
  await settle();
  assert.equal(answers.length, 2, 'a change of filter asks at once');

  answers[1].resolve({ requests: REQUESTS.all });
  await fresh;
  answers[0].resolve({ requests: REQUESTS.mine });
  await slow;
  assert.equal($('run-requests').children.length, 3, 'everyone’s, the latest asked for');
  assert.equal($('run-owner').value, 'all');
  // Loading is over, so the next poll loads again.
  a.loadRequests();
  await settle();
  assert.equal(answers.length, 3);
});
