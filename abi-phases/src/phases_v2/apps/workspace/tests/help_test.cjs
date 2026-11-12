const assert = require('node:assert/strict');
const { readFileSync, readdirSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const { ROOT, fire, install, load } = require('./harness.cjs');

const read = (...parts) => readFileSync(join(ROOT, ...parts), 'utf8');
const sectionFiles = () => readdirSync(join(ROOT, 'js', 'sections')).filter((name) => name.endsWith('.js'));
const unescape = (html) => html.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

// The sidebar's sections, in order, as index.html lists them.
function sidebar() {
  return [...read('index.html').matchAll(/<a href="#\/(\w+)" data-nav="(\w+)"[^>]*>.*?<span>([^<]+)<\/span><\/a>/g)]
    .map(([, route, nav, title]) => ({ route, nav, title }));
}

async function helpSection() {
  const dom = install();
  const { create } = await load('sections/help.js');
  const section = create(dom.$('section-help'));
  return { dom, section, $: dom.$ };
}

const html = (dom) => dom.$('section-help').innerHTML;

// Every page and every entry, rendered.
async function everyPage() {
  const { dom, section } = await helpSection();
  const { TOPICS } = await load('help/topics.js');
  const pages = [];
  for (const topic of TOPICS) {
    section.show({ parts: [topic.id] });
    pages.push({ topic, html: html(dom) });
  }
  return pages;
}

test('Help is a section with routes for its index, its pages and their entries', async () => {
  install();
  const { parse, SECTIONS } = await load('router.js');
  assert.ok(SECTIONS.includes('help'));
  assert.deepEqual(parse('#/help').parts, []);
  assert.deepEqual(parse('#/help/library').parts, ['library']);
  const deep = parse('#/help/library/schedules');
  assert.equal(deep.section, 'help');
  assert.deepEqual(deep.parts, ['library', 'schedules']);
});

test('the index is Getting started, then every help page', async () => {
  const { dom, section, $ } = await helpSection();
  const { TOPICS } = await load('help/topics.js');
  for (const [index, parts] of [[], ['getting-started']].entries()) {
    section.show({ parts });
    const page = html(dom);
    assert.match(page, /<h1 id="help-title" tabindex="-1">Getting started<\/h1>/);
    const order = ['Sign in', 'Open the HealthyPhases app', 'The four steps', 'All help pages'].map((title) => page.indexOf(`tabindex="-1">${title}</h2>`));
    assert.ok(order.every((i) => i > 0) && order.join() === [...order].sort((a, b) => a - b).join(), 'Getting started comes before the list of pages');
    for (const topic of TOPICS.slice(1)) assert.match(page, new RegExp(`<a href="#/help/${topic.id}">${topic.title}</a>`), topic.id);
    if (index === 0) assert.equal($('help-title').focused, undefined, 'opening the app on Help leaves focus alone');
  }
  assert.equal(dom.document.title, 'Help · HealthyPhases');
});

test('a page lists its entries at the top and links to the section it describes', async () => {
  const { dom, section } = await helpSection();
  section.show({ parts: ['library'] });
  const page = html(dom);
  assert.match(page, /<h1 id="help-title" tabindex="-1">Library<\/h1>/);
  assert.match(page, /<a class="button small" href="#\/library">Open Library<\/a>/);
  const toc = page.match(/<nav class="card help-toc"[^>]*>([\s\S]*?)<\/nav>/)[1];
  assert.match(toc, /On this page/);
  for (const entry of ['papers', 'search', 'downloads', 'schedules', 'enrichment']) {
    assert.match(toc, new RegExp(`href="#/help/library/${entry}"`), entry);
    assert.ok(page.indexOf(`id="help-library-${entry}"`) > page.indexOf('help-toc'), `${entry} follows the list`);
  }
  assert.match(page, /<a href="#\/help\/library" aria-current="page">Library<\/a>/);
  assert.equal(dom.document.title, 'Library · Help · HealthyPhases');
});

test('the required entries exist on their pages', async () => {
  const { resolve } = await load('sections/help.js');
  const required = {
    library: ['papers', 'search', 'downloads', 'schedules', 'enrichment'],
    prompts: ['editor', 'try-it', 'versions', 'compare-with'],
    runs: ['new-run', 'saved-configurations'],
    glossary: ['paper', 'ingested', 'chunk', 'chunker', 'prompt', 'prompt-version', 'model', 'run', 'findings', 'collection', 'collection-member', 'saved-search'],
    faq: ['sign-in-code', 'not-ingested', 'run-failed', 'no-results', 'older-version', 'upload-pdfs'],
  };
  for (const [topic, entries] of Object.entries(required)) {
    for (const entry of entries) {
      const view = resolve([topic, entry]);
      assert.equal(view.note, '', `${topic}/${entry}`);
      assert.equal(view.entry.id, entry);
    }
  }
  for (const topic of ['getting-started', 'overview', 'library', 'collections', 'prompts', 'runs', 'results', 'search', 'glossary', 'assistant', 'faq']) {
    assert.equal(resolve([topic]).topic.id, topic);
  }
});

test('an entry link opens its page scrolled to the entry, with focus on its heading', async () => {
  const { dom, section, $ } = await helpSection();
  section.show({ parts: ['runs', 'saved-configurations'] });
  assert.match(html(dom), /<h1 id="help-title" tabindex="-1">Runs<\/h1>/);
  assert.equal($('help-runs-saved-configurations').focused, true);
  assert.deepEqual($('help-runs-saved-configurations').scrolled, { block: 'start' });
  assert.match(html(dom), /<h2 id="help-runs-saved-configurations" tabindex="-1">Saved configurations<\/h2>/);
  assert.match(html(dom), /href="#\/help\/runs\/saved-configurations" aria-current="true"/);
  assert.equal(section.keepsFocus(), true);
  section.show({ parts: ['runs'] });
  assert.equal(section.keepsFocus(), false);
  // The index's list of pages is an entry too, marked when it is the one open.
  section.show({ parts: ['getting-started', 'pages'] });
  assert.match(html(dom), /href="#\/help\/getting-started\/pages" aria-current="true">All help pages/);
});

test('opening the app at an entry scrolls to it and leaves focus where Nexus has it', async () => {
  const dom = install({ hash: '#/help/faq/run-failed' });
  const { boot } = await load('shell.js');
  boot({ every() {} });
  const heading = { focus() { heading.focused = true; } };
  dom.$('section-help').querySelector = () => heading;
  const entry = dom.$('help-faq-run-failed');
  assert.deepEqual(entry.scrolled, { block: 'start' });
  assert.equal(entry.focused, undefined, 'the entry does not take focus on the first show');
  assert.equal(heading.focused, undefined, 'nor does the page heading');

  // Once the app is open, following an entry link focuses it.
  dom.location.hash = '#/help/faq/no-results';
  dom.hashchange();
  assert.equal(dom.$('help-faq-no-results').focused, true);
});

test('a link to the entry already open scrolls to it and focuses it again', async () => {
  const dom = install({ hash: '#/help/faq/run-failed' });
  const { boot } = await load('shell.js');
  boot({ every() {} });
  const entry = dom.$('help-faq-run-failed');
  entry.scrolled = undefined;
  const link = { getAttribute: (key) => (key === 'href' ? '#/help/faq/run-failed' : null) };
  let prevented = false;
  await fire(dom.$('section-help'), 'click', { target: { closest: () => link }, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.deepEqual(entry.scrolled, { block: 'start' });
  assert.equal(entry.focused, true);

  // A link elsewhere is left to the browser.
  prevented = false;
  const other = { getAttribute: () => '#/help/faq/no-results' };
  await fire(dom.$('section-help'), 'click', { target: { closest: () => other }, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
});

test('moving between help pages focuses the new page heading', async () => {
  const { section, $ } = await helpSection();
  section.show({ parts: [] });
  section.show({ parts: ['faq'] });
  assert.equal($('help-title').focused, true);
  section.hide();
  $('help-title').focused = undefined;
  section.show({ parts: ['glossary'] });
  assert.equal($('help-title').focused, undefined, 'coming back from another section, the shell focuses the heading');
});

test('an unknown topic shows the index with a note, and an unknown entry its page with a note', async () => {
  const { dom, section, $ } = await helpSection();
  section.show({ parts: ['nowhere'] });
  assert.match(html(dom), /<h1 id="help-title" tabindex="-1">Getting started<\/h1>/);
  assert.match(html(dom), /<p id="help-note" class="note" role="status">There is no help page called “nowhere”/);
  assert.match(html(dom), /All help pages/);

  section.show({ parts: ['prompts', 'nothing-here'] });
  assert.match(html(dom), /<h1 id="help-title" tabindex="-1">Prompts<\/h1>/);
  assert.match(html(dom), /“Prompts” has no part called “nothing-here”, so it opens at the top\./);
  assert.match(html(dom), /id="help-prompts-try-it"/, 'the whole page is shown');
  assert.equal($('help-title').focused, true);
  assert.equal(section.keepsFocus(), false);

  section.show({ parts: ['nowhere', 'at-all'] });
  assert.match(html(dom), /There is no help page called “nowhere”/);
  section.show({ parts: ['faq'] });
  assert.doesNotMatch(html(dom), /help-note/);
});

test('entered from another section at an entry, the entry keeps focus; at a page, the heading takes it', async () => {
  const dom = install({ hash: '#/library' });
  const { boot } = await load('shell.js');
  boot({ every() {} });
  const heading = { focus() { heading.focused = (heading.focused || 0) + 1; } };
  dom.$('section-help').querySelector = () => heading;

  dom.location.hash = '#/help/library/schedules';
  dom.hashchange();
  assert.equal(dom.$('help-library-schedules').focused, true);
  assert.equal(heading.focused, undefined, 'the shell leaves focus on the entry');
  assert.equal(dom.navLinks.find((l) => l.dataset.nav === 'help').attributes['aria-current'], 'page');

  dom.location.hash = '#/runs';
  dom.hashchange();
  dom.location.hash = '#/help/runs';
  dom.hashchange();
  assert.equal(heading.focused, 1);
});

// Whether `label` is a whole UI string in `source`: the text of an element, or a
// string literal, never part of a longer label ("Add" is not in "Add papers").
function showsLabel(source, label) {
  const literal = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?:>\\s*|['"\`])${literal}(?:\\s*<|['"\`])`).test(source);
}

test('a label counts only as a whole UI string', () => {
  assert.ok(showsLabel('<button>Add</button>', 'Add'));
  assert.ok(showsLabel("button('Add', run)", 'Add'));
  assert.ok(showsLabel('<span>\n  Save version\n</span>', 'Save version'));
  assert.ok(!showsLabel('<button>Add papers</button>', 'Add'), 'a renamed short label fails');
  assert.ok(!showsLabel("'Open collection'", 'Open'));
  assert.ok(!showsLabel('<p>Papers you saved</p>', 'Papers'));
  assert.ok(!showsLabel('const Search = 1;', 'Search'));
});

test('every label the help marks with ui() is written the same way in the app', async () => {
  install();
  const { UI_LABELS } = await load('help/topics.js');
  // The help's own section is left out: a label must exist where the app shows it.
  const sources = [read('index.html'), read('js', 'ui.js'), ...sectionFiles().filter((f) => f !== 'help.js').map((f) => read('js', 'sections', f))].join('\n');
  assert.ok(UI_LABELS.size > 150, `${UI_LABELS.size} labels`);
  const missing = [...UI_LABELS].filter((label) => !showsLabel(sources, label));
  assert.deepEqual(missing, []);
});

test('every bold label in the help went through ui() or nexus()', async () => {
  const pages = await everyPage();
  const { UI_LABELS, NEXUS_LABELS } = await load('help/topics.js');
  for (const { topic, html: page } of pages) {
    for (const [, label] of page.matchAll(/<strong class="ui-label">(.*?)<\/strong>/g)) {
      const text = unescape(label);
      assert.ok(UI_LABELS.has(text) || NEXUS_LABELS.has(text), `${topic.id}: ${text}`);
    }
  }
  // The Nexus screens around the app, checked by hand against Nexus itself.
  assert.deepEqual([...NEXUS_LABELS].sort(), ['Apps', 'Email', 'HealthyPhases', 'Send sign-in code', 'Sign-in code', 'Use a different email', 'Verify code']);
});

test('the help says nothing about the sign-in that the brief rules out', async () => {
  const pages = await everyPage();
  const text = pages.map((p) => p.html).join('\n');
  assert.doesNotMatch(text, /[\w.+-]+@[\w-]+\.[\w.]+/, 'no email address is published');
  assert.doesNotMatch(text, /\b(AI|LLM|artificial intelligence|language model)\b/i);
  const faq = pages.find((p) => p.topic.id === 'faq').html;
  for (const step of ['address you were invited with', 'spam or junk folder', 'Wait a few minutes, then request a new code', 'contact the person who invited you']) {
    assert.ok(faq.includes(step), step);
  }
});

test('the Assistant page reuses how to open it and the example requests', async () => {
  install();
  const { ASSISTANT_HINT, ASSISTANT_EXAMPLES } = await load('sections/overview.js');
  const pages = await everyPage();
  const page = pages.find((p) => p.topic.id === 'assistant').html;
  assert.ok(page.includes(ASSISTANT_HINT), 'the hint');
  for (const example of ASSISTANT_EXAMPLES) assert.ok(page.includes(`“${example}”`), example);
  for (const part of ['runs', 'downloading PubMed papers', 'schedule', 'OpenAlex enrichment', 'cannot delete, archive or remove anything']) {
    assert.ok(page.includes(part), part);
  }
});

test('every sidebar section has a help page, and a Learn more link to it in its page head', async () => {
  const dom = install({ hash: '#/overview' });
  const { boot } = await load('shell.js');
  const { SECTIONS } = await load('router.js');
  const { SECTION_TOPICS, TOPICS } = await load('help/topics.js');
  const shell = boot({ every() {} });
  const nav = sidebar();
  assert.deepEqual(nav.map((n) => n.nav), SECTIONS, 'the sidebar lists the router’s sections in order');
  for (const { nav: name, title } of nav) {
    if (name === 'help') continue; // Help is the help itself.
    shell.section(name);
    const topic = TOPICS.find((t) => t.id === SECTION_TOPICS[name]);
    assert.ok(topic, name);
    assert.equal(topic.title, title);
    const head = dom.$(`section-${name}`).innerHTML.match(/<header class="page-head">([\s\S]*?)<\/header>/)[1];
    assert.match(head, new RegExp(`<p class="lede">[^]*<a class="learn-more" href="#/help/${topic.id}" aria-label="Learn more about ${title}">Learn more</a></p>`), name);
  }
});

test('the sidebar foot and the Overview card link to the Assistant’s help page', async () => {
  const foot = read('index.html').match(/<p class="sidebar-foot">(.*?)<\/p>/)[1];
  assert.match(foot, /<a href="#\/help\/assistant">Help → The Assistant<\/a>/);
  const dom = install();
  const { create } = await load('sections/overview.js');
  create(dom.$('section-overview'), { href: () => '#', navigate() {} });
  const card = dom.$('section-overview').innerHTML.match(/<section class="card assistant"[\s\S]*?<\/section>/)[0];
  assert.match(card, /href="#\/help\/assistant"/);
});

test('every help link in the app and in the help opens a page or an entry', async () => {
  const dom = install({ hash: '#/overview' });
  const { boot } = await load('shell.js');
  const { parse, SECTIONS } = await load('router.js');
  const { resolve } = await load('sections/help.js');
  const shell = boot({ every() {} });
  const links = new Set();
  const collect = (text) => { for (const [link] of text.matchAll(/#\/help(?:\/[\w-]+){0,2}(?![\w/-])/g)) links.add(link); };

  // What is written down: the page, every module, and the help pages themselves.
  collect(read('index.html'));
  const files = ['ui.js', 'router.js', 'shell.js', ...sectionFiles().map((f) => `sections/${f}`), 'help/topics.js'];
  for (const file of files) {
    const text = read('js', ...file.split('/'));
    collect(text);
    // helpHref and learnMore take literal names, so each call can be checked here.
    if (!['ui.js', 'sections/help.js', 'help/topics.js'].includes(file)) {
      const calls = [...text.matchAll(/\b(?:helpHref|learnMore)\(/g)].length;
      const literal = [...text.matchAll(/\b(?:helpHref|learnMore)\(\s*'([\w-]+)'(?:\s*,\s*'([\w-]*)')?/g)];
      assert.equal(literal.length, calls, `${file}: every help link names its page literally`);
      for (const [, topic, entry] of literal) links.add(`#/help/${topic}${entry ? `/${entry}` : ''}`);
    }
  }
  // What is drawn: every section's page, and every help page.
  for (const name of SECTIONS) { shell.section(name); collect(dom.$(`section-${name}`).innerHTML); }
  const pages = await everyPage();
  for (const { html: page } of pages) collect(page);

  assert.ok(links.size > 60, `${links.size} links`);
  for (const link of links) {
    const route = parse(link);
    assert.equal(route.section, 'help', link);
    const view = resolve(route.parts);
    assert.equal(view.note, '', link);
    if (route.parts[1]) assert.equal(view.entry?.id, route.parts[1], link);
  }

  // And the help's links into the app name real sections.
  for (const { topic, html: page } of pages) {
    for (const [, target] of page.matchAll(/href="(#\/[^"]*)"/g)) {
      const name = target.replace(/^#\//, '').split(/[/?]/)[0];
      assert.ok(SECTIONS.includes(name), `${topic.id}: ${target}`);
    }
  }
});
