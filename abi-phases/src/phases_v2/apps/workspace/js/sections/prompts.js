// Prompts: the prompt library. Saving changed text creates a new version; earlier
// versions stay available to the runs that used them. Below the editor, Try it
// runs the text on a few chunks of a paper, and Versions lists and compares the
// texts saved under the same name.
//
// A prompt belongs to whoever created its name, and only they save new versions
// of it. Shared prompts (the built-in ones, and those from before owners were
// recorded) and other people's are read-only here: Duplicate prompt makes a
// copy of your own. The library shows your own prompts unless Everyone's is chosen.
//
// #/prompts opens the first prompt, #/prompts/<prompt_id> a given version and
// #/prompts/new an empty form.

import { phases } from '../api.js';
import {
  $, EVERYONE, MINE, action, emptyState, esc, fill, helpHref, learnMore, message, ownerFilter, ownerLabel, shownFor,
} from '../ui.js';
import { createTry, shortId, template as tryTemplate } from './prompt_try.js';
import { createVersions, template as versionsTemplate } from './prompt_versions.js';

export const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 _-]*$/;

export const OUTPUT_KEYS = ['results', 'what', 'causes', 'how', 'when', 'where', 'effects', 'conditional_statement', 'relations'];

// Versions share a name; a short fingerprint tells them apart only when needed.
export function promptLabel(prompt, prompts) {
  const versions = prompts.filter((p) => p.name === prompt.name).length;
  return versions > 1 ? `${prompt.name} (version ${prompt.prompt_id.slice(-8)})` : prompt.name;
}

// The characters Python's str.strip() removes, which JavaScript's trim() does
// not quite match (it keeps \x1c-\x1f and \x85, and removes \ufeff).
const PY_SPACE = '\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const PY_STRIP = new RegExp(`^[${PY_SPACE}]+|[${PY_SPACE}]+$`, 'g');
const PY_RSTRIP = new RegExp(`[${PY_SPACE}]+$`);

// What makes two prompt names the same name, as the server compares them
// (strip, then casefold). Upper- then lower-casing folds what toLowerCase()
// alone does not, so "Straße" and "STRASSE" are one name here too.
export const nameKey = (name) => String(name || '').replace(PY_STRIP, '').toUpperCase().toLowerCase();

// Why the name in the editor cannot take a new version, or '' when it can:
// a name nobody has yet is free, one you own is yours.
export function readOnlyReason(name, prompts) {
  const taken = prompts.find((p) => nameKey(p.name) === nameKey(name));
  return taken && taken.can_change === false ? taken.read_only_reason || 'Only this prompt’s owner can save new versions of it.' : '';
}

// The longest prompt name the server accepts.
export const MAX_NAME = 100;

// "<name> copy", numbered when that name is taken, as the server names a copy.
// The name is cut, in characters as the server counts them, so that the suffix
// always fits in MAX_NAME.
export function copyName(name, prompts) {
  const taken = new Set(prompts.map((p) => nameKey(p.name)));
  const base = Array.from(String(name || 'Prompt'));
  for (let n = 1; ; n += 1) {
    const suffix = n === 1 ? ' copy' : ` copy ${n}`;
    const candidate = base.slice(0, MAX_NAME - suffix.length).join('').replace(PY_RSTRIP, '') + suffix;
    if (!taken.has(nameKey(candidate))) return candidate;
  }
}

export function formatExample(key) {
  return key === 'relations'
    ? 'Structured process and disposition relations. Duplicate the probabilistic_processes prompt for its full format.'
    : `Return up to 10 non-empty strings: ${JSON.stringify({ [key]: ['An extracted claim'] })}`;
}

const template = () => `
  <header class="page-head"><p class="eyebrow">Step 2 · Prepare prompts</p><h1 tabindex="-1">Prompts</h1>
    <p class="lede">Write and refine the instructions that extract findings from each chunk of a paper. ${learnMore('prompts', '', 'Prompts')}</p></header>
  <div class="split">
    <section class="card">
      <div class="card-head"><h2>Prompt library</h2><button id="pr-new" type="button">New prompt</button></div>
      <div class="row">${ownerFilter('pr-owner', 'prompts')}${learnMore('prompts', 'owners', 'whose prompts are shown')}</div>
      <label for="pr-search">Find a prompt</label><input id="pr-search" type="search" placeholder="Search by name">
      <div id="pr-list" class="resource-list" aria-label="Prompts"></div>
    </section>
    <div class="stack">
    <section class="card">
      <div class="card-head"><h2 id="pr-title">New prompt</h2>${learnMore('prompts', 'editor', 'the editor')}</div>
      <form id="pr-form"><div class="stack">
        <div><label for="pr-name">Prompt name</label><input id="pr-name" required maxlength="100"></div>
        <div><label for="pr-output">Output format</label><select id="pr-output"></select><p id="pr-format" class="hint"></p></div>
        <div><label for="pr-template">Prompt text</label><textarea id="pr-template" required spellcheck="false" maxlength="100000" placeholder="Extract claims from the following text: {chunk_text}"></textarea>
          <p class="hint">Include <code>{chunk_text}</code> where the document text should appear.</p></div>
      </div>
      <p id="pr-version" class="hint"></p>
      <p id="pr-owner-line" class="hint" role="status"></p>
      <div class="row actions"><button id="pr-save" class="primary">Save as new version</button><button type="button" id="pr-duplicate">Duplicate prompt</button><button type="button" id="pr-use" disabled>Use in new run</button></div>
      <p id="pr-message" class="msg" role="status"></p></form>
    </section>
    ${tryTemplate()}
    ${versionsTemplate()}
    </div>
  </div>`;

export function create(root, ctx) {
  root.innerHTML = template();
  const state = { prompts: [], promptId: '', current: '', loaded: false, owner: MINE };
  $('pr-owner').value = MINE;
  fill($('pr-output'), OUTPUT_KEYS.map((key) => ({ key })), 'key', (p) => p.key);

  const showFormat = () => { $('pr-format').textContent = formatExample($('pr-output').value); };

  // Only the saved, unedited version can go into a run; a draft must be saved first.
  function setUsable(usable) {
    $('pr-use').disabled = !usable;
    $('pr-try-use').disabled = !usable;
  }

  // Saving is for names that are free or yours. Under a shared or someone else's
  // name both Save buttons are disabled, the reason is said, and Duplicate prompt
  // becomes the main action.
  function updateSaving() {
    const name = $('pr-name').value.trim();
    const reason = readOnlyReason(name, state.prompts);
    for (const id of ['pr-save', 'pr-try-save']) {
      $(id).disabled = Boolean(reason);
      $(id).title = reason;
    }
    $('pr-duplicate').className = reason ? 'primary' : '';
    const owned = state.prompts.find((p) => nameKey(p.name) === nameKey(name));
    let line = '';
    if (reason) line = `Read-only: ${reason}`;
    else if (owned?.owned_by_you) line = 'You own this prompt: only you save new versions of it.';
    else if (owned) line = 'Shared prompt.';
    else if (name) line = 'Saving makes this prompt yours.';
    message('pr-owner-line', line, reason ? 'warn' : '');
    return reason;
  }

  // The checks the server makes, said before sending: the editor's Save and the
  // Try panel's Save both go through save(), which runs them.
  function draftProblem({ name, template }) {
    if (!name) return 'Name the prompt before saving it.';
    if (!NAME_PATTERN.test(name)) return 'Use letters, digits, spaces, _ or - in the name, starting with a letter or digit.';
    if (!template.includes('{chunk_text}')) return 'The prompt text must contain {chunk_text}, where each chunk\'s text is placed.';
    return '';
  }

  const formValues = () => ({ name: $('pr-name').value.trim(), template: $('pr-template').value, output_key: $('pr-output').value });

  // What Try it sends: the saved version when the editor still shows it, else the draft.
  function trySource() {
    const values = formValues();
    const saved = state.prompts.find((p) => p.prompt_id === state.promptId);
    if (saved && saved.name === values.name && saved.template === values.template && saved.output_key === values.output_key) {
      return { body: { prompt_id: saved.prompt_id }, label: `Version ${shortId(saved.prompt_id)}, now` };
    }
    return { body: { draft: { ...values, name: values.name || 'Draft' } }, label: 'Text in the editor, now' };
  }

  const tryPanel = createTry(ctx, { source: trySource });
  const versions = createVersions(ctx, {
    draft: () => $('pr-template').value,
    open: (id) => ctx.navigate(ctx.href('prompts', [id])),
    use: (id) => useInRun(id),
  });

  // The open prompt's versions, offered as the baseline: by default the version
  // the editor was opened from, so a draft is compared with what it changes.
  async function loadVersions(name, current, baseline = current) {
    const list = await versions.load(name, current);
    if (!list) return;
    tryPanel.setBaselines(list.map((v) => ({ prompt_id: v.prompt_id, label: v.prompt_id === current ? 'in the editor' : '' })), baseline || '');
  }

  function renderList() {
    $('pr-list').replaceChildren();
    const term = $('pr-search').value.toLowerCase();
    const shown = state.prompts.filter((p) => shownFor(state.owner, p));
    const prompts = shown.filter((p) => p.name.toLowerCase().includes(term));
    if (!prompts.length) {
      if (shown.length) $('pr-list').innerHTML = emptyState('No prompt matches this search. Clear it to see every prompt.');
      else if (state.owner === MINE && state.prompts.length) {
        $('pr-list').innerHTML = emptyState('You have no prompts of your own yet. Write one with New prompt, or choose Everyone’s, open a prompt and use Duplicate prompt.', [helpHref('prompts', 'duplicate'), 'How to duplicate a prompt']);
      } else $('pr-list').innerHTML = emptyState('No prompts yet. Write one with New prompt.', [helpHref('prompts', 'editor'), 'How to write a prompt']);
    }
    for (const prompt of prompts) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'resource';
      button.setAttribute('aria-pressed', String(prompt.prompt_id === state.promptId));
      button.innerHTML = `${esc(promptLabel(prompt, state.prompts))}<small>${esc(prompt.output_key)} · ${esc(ownerLabel(prompt))}</small>`;
      button.addEventListener('click', () => ctx.navigate(ctx.href('prompts', [prompt.prompt_id])));
      $('pr-list').append(button);
    }
  }

  function edit(prompt) {
    state.promptId = prompt?.prompt_id || '';
    $('pr-name').value = prompt?.name || '';
    $('pr-template').value = prompt?.template || '';
    $('pr-output').value = prompt?.output_key || 'results';
    showFormat();
    $('pr-title').textContent = prompt ? `Edit ${prompt.name}` : 'New prompt';
    $('pr-version').textContent = prompt
      ? 'Saving changes creates a new version. This version stays available to the runs that used it.'
      : 'Saving adds this prompt to the library.';
    setUsable(Boolean(prompt));
    message('pr-message');
    updateSaving();
    renderList();
    loadVersions(prompt?.name || '', prompt?.prompt_id || '');
  }

  async function reload() {
    state.prompts = (await phases('/prompts')).prompts;
    state.loaded = true;
    renderList();
    updateSaving();
  }

  function chooseOwner(owner) {
    state.owner = owner === EVERYONE ? EVERYONE : MINE;
    $('pr-owner').value = state.owner;
    renderList();
  }

  async function save() {
    const previousId = state.promptId;
    const problem = draftProblem(formValues()) || readOnlyReason(formValues().name, state.prompts);
    if (problem) throw new Error(problem);
    const saved = await phases('/prompts', {
      method: 'POST', body: formValues(),
    });
    await reload();
    edit(state.prompts.find((p) => p.prompt_id === saved.prompt_id) || saved);
    state.current = saved.prompt_id;
    ctx.replace(ctx.href('prompts', [saved.prompt_id]));
    // The new version replaces the one it was edited from in the next run.
    ctx.section('runs').selectPrompts([saved.prompt_id], previousId && previousId !== saved.prompt_id ? [previousId] : []);
    message('pr-message', 'Prompt saved and selected for your next run.', 'ok');
    // Tries go on comparing with the version this one was edited from.
    await loadVersions(saved.name, saved.prompt_id, previousId && previousId !== saved.prompt_id ? previousId : saved.prompt_id);
    return saved;
  }

  function duplicate() {
    state.promptId = '';
    state.current = 'new';
    ctx.replace(ctx.href('prompts', ['new']));
    $('pr-name').value = copyName($('pr-name').value.trim(), state.prompts);
    setUsable(false);
    $('pr-title').textContent = 'New prompt';
    $('pr-version').textContent = 'Saving adds this copy to the library, as your own prompt.';
    updateSaving();
    $('pr-name').focus();
    renderList();
  }

  // Saving from the Try panel answers there, beside the results it was judged on.
  async function saveFromTry() {
    const saved = await save();
    message('pr-try-message', `Saved as version ${shortId(saved.prompt_id)} and selected for your next run.`, 'ok');
    return saved;
  }

  function useInRun(promptId = state.promptId) {
    ctx.section('runs').selectPrompts([promptId]);
    ctx.navigate(ctx.href('runs', ['new']));
  }

  $('pr-output').addEventListener('change', showFormat);
  $('pr-new').addEventListener('click', () => ctx.navigate(ctx.href('prompts', ['new'])));
  $('pr-search').addEventListener('input', renderList);
  $('pr-owner').addEventListener('change', () => chooseOwner($('pr-owner').value));
  $('pr-form').addEventListener('submit', (event) => { event.preventDefault(); action($('pr-save'), 'pr-message', save); });
  // An edited form is not the saved version any more, so it cannot be added to a run yet.
  $('pr-form').addEventListener('input', () => {
    setUsable(false);
    updateSaving();
    // A comparison with the editor follows what is typed.
    if ([$('pr-ver-a').value, $('pr-ver-b').value].includes('draft')) versions.compare();
  });
  $('pr-duplicate').addEventListener('click', duplicate);
  $('pr-use').addEventListener('click', () => useInRun());
  $('pr-try-use').addEventListener('click', () => useInRun());
  $('pr-try-save').addEventListener('click', () => action($('pr-try-save'), 'pr-try-message', saveFromTry));

  function select(id) {
    if (id === 'new') return edit(null);
    // With no id, the first prompt the library shows; with none shown, a new one.
    const prompt = state.prompts.find((p) => p.prompt_id === id) || (!id && state.prompts.find((p) => shownFor(state.owner, p)));
    if (!prompt) {
      edit(null);
      if (id) message('pr-message', 'That prompt version was not found. Choose one from the library.', 'warn');
      return undefined;
    }
    if (!id) ctx.replace(ctx.href('prompts', [prompt.prompt_id]));
    return edit(prompt);
  }

  // `entering` means coming back from another section: the lists are re-read first,
  // so anything created elsewhere is there, and an open draft is kept. Within the
  // section, following a link to what is already open (New, or a list item) re-renders it.
  async function show(route, { entering = false } = {}) {
    const id = route.parts[0] || '';
    if (!state.loaded || entering) {
      try { await reload(); } catch (error) { message('pr-message', `Could not load prompts: ${error.message}`, 'bad'); if (!state.loaded) return; }
    }
    if (!tryPanel.state.loaded) {
      tryPanel.load().catch((error) => message('pr-try-message', `Could not load models, chunkers or papers: ${error.message}`, 'bad'));
    }
    // The sidebar link has no id: come back to what was open, draft included.
    if (!id && state.current) { ctx.replace(ctx.href('prompts', [state.current])); return; }
    if (entering && state.current && id === state.current) return;
    state.current = id;
    select(id);
    if (!id) state.current = state.promptId || 'new';
  }

  async function refresh() {
    try { await reload(); } catch (error) { message('pr-message', `Could not refresh prompts: ${error.message}`, 'bad'); }
    // New runs change what each version was used for; keep the chosen baseline.
    if (versions.state.name) await loadVersions(versions.state.name, versions.state.current, $('pr-try-baseline').value);
  }

  return { show, hide() {}, refresh, state, tryPanel, versions, actions: { save, saveFromTry, duplicate, useInRun, edit, reload, chooseOwner, tryIt: tryPanel.run } };
}
