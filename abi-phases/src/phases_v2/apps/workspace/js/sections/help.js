// Help: Getting started and the list of pages at #/help, a page at
// #/help/<topic>, and a page opened at one of its entries at
// #/help/<topic>/<entry>. The pages themselves are in ../help/topics.js.
//
// An unknown topic shows the index, and an unknown entry its page, each with a
// note: a help link never lands on a blank page.

import { $, esc, helpHref } from '../ui.js';
import { findTopic, INDEX_TOPIC, TOPICS } from '../help/topics.js';

// The index lists every page after Getting started's own entries.
const PAGES_ENTRY = 'pages';

export const headingId = (topic, entry) => `help-${topic}-${entry}`;

const topicHref = (topic) => (topic.id === INDEX_TOPIC ? '#/help' : helpHref(topic.id));

function pagesBody() {
  return `<ul class="help-index">${TOPICS.filter((t) => t.id !== INDEX_TOPIC).map((t) => `
    <li><a href="${topicHref(t)}">${esc(t.title)}</a><span class="muted">${esc(t.lede)}</span></li>`).join('')}</ul>`;
}

// The entries a page shows, the index's list of pages included.
export function entriesOf(topic) {
  if (topic.id !== INDEX_TOPIC) return topic.entries;
  return [...topic.entries, { id: PAGES_ENTRY, title: 'All help pages', body: pagesBody() }];
}

// What a route opens: { topic, entry, note }. `entry` is null for the top of the page.
export function resolve(parts = []) {
  const [topicId = '', entryId = ''] = parts;
  const topic = findTopic(topicId || INDEX_TOPIC);
  if (!topic) {
    return { topic: findTopic(INDEX_TOPIC), entry: null, note: `There is no help page called “${topicId}”. Here is Getting started, with every help page at the end.` };
  }
  if (!entryId) return { topic, entry: null, note: '' };
  const entry = entriesOf(topic).find((e) => e.id === entryId);
  if (!entry) return { topic, entry: null, note: `“${topic.title}” has no part called “${entryId}”, so it opens at the top.` };
  return { topic, entry, note: '' };
}

// Every route the help answers: each page, and each page at each entry.
export function routes() {
  return TOPICS.flatMap((topic) => [
    topicHref(topic),
    helpHref(topic.id),
    ...entriesOf(topic).map((entry) => helpHref(topic.id, entry.id)),
  ]);
}

function template({ topic, entry, note }) {
  const entries = entriesOf(topic);
  const open = topic.section
    ? `<p class="help-open"><a class="button small" href="#/${topic.section}">Open ${esc(topic.title)}</a></p>` : '';
  const toc = entries.map((e) => `<li><a href="${helpHref(topic.id, e.id)}"${e === entry ? ' aria-current="true"' : ''}>${esc(e.title)}</a></li>`).join('');
  const pages = TOPICS.map((t) => `<li><a href="${topicHref(t)}"${t === topic ? ' aria-current="page"' : ''}>${esc(t.title)}</a></li>`).join('');
  return `
  <header class="page-head"><p class="eyebrow">Help</p><h1 id="help-title" tabindex="-1">${esc(topic.title)}</h1>
    <p class="lede">${esc(topic.lede)}</p>${open}</header>
  ${note ? `<p id="help-note" class="note" role="status">${esc(note)}</p>` : ''}
  <div class="help-layout">
    <div class="help-page">
      <nav class="card help-toc" aria-labelledby="help-toc-title"><h2 id="help-toc-title">On this page</h2><ul>${toc}</ul></nav>
      ${entries.map((e) => `<section class="card help-entry" aria-labelledby="${headingId(topic.id, e.id)}">
        <h2 id="${headingId(topic.id, e.id)}" tabindex="-1">${esc(e.title)}</h2>${e.body}</section>`).join('')}
    </div>
    <nav class="card help-pages" aria-labelledby="help-pages-title"><h2 id="help-pages-title">Help pages</h2><ul>${pages}</ul></nav>
  </div>`;
}

export function create(root) {
  // Shown since it was last hidden: moving between help pages then focuses the
  // new page, while the shell focuses it when Help is entered from elsewhere.
  let active = false;

  function show(route) {
    const view = resolve(route.parts);
    const within = active;
    active = true;
    root.innerHTML = template(view);
    document.title = view.topic.id === INDEX_TOPIC ? 'Help · HealthyPhases' : `${view.topic.title} · Help · HealthyPhases`;
    if (view.entry) {
      const heading = $(headingId(view.topic.id, view.entry.id));
      heading.scrollIntoView?.({ block: 'start' });
      heading.focus();
    } else if (within) {
      $('help-title').focus();
    }
    return view;
  }

  return {
    show,
    hide() { active = false; },
    // The pages are part of the app: there is nothing to read again.
    refresh() {},
    // An entry gets focus from show(), and keeps it.
    keepsFocus: (route) => Boolean(resolve(route.parts).entry),
  };
}
