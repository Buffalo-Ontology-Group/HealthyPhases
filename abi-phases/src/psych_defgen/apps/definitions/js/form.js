// The new-definition form. Its limits, defaults and models come from
// GET /settings; a deployment missing a required key gets a notice naming it
// and a disabled form, since the API would refuse the run anyway (409).

import { getSettings, startRun } from './api.js';
import { runHref } from './router.js';
import { $, listText, message } from './ui.js';

export const TERM_MAX = 200;

const FIELDS = {
  term: 'def-term',
  max_results: 'def-max-results',
  top_k: 'def-top-k',
  model: 'def-model',
};

export function missingNotice(missing = []) {
  if (!missing.length) return '';
  const one = missing.length === 1;
  return `${listText(missing)} ${one ? 'is' : 'are'} not set on this deployment, so no definition can be produced yet. `
    + `Ask an administrator to add ${one ? 'it' : 'them'} to the deployment's environment and restart it.`;
}

function whole(raw, limits) {
  const text = String(raw ?? '').trim();
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  return value >= limits.min && value <= limits.max ? value : null;
}

// Mirrors the API's checks, so a mistake is explained before anything is sent.
export function validate(values, settings) {
  const errors = {};
  const term = String(values.term ?? '').trim();
  if (!term) errors.term = 'Enter a construct, such as loneliness.';
  else if ([...term].length > TERM_MAX) errors.term = `The term must be ${TERM_MAX} characters or fewer.`;
  const maxResults = whole(values.max_results, settings.max_results);
  if (maxResults === null) errors.max_results = `Max results must be a whole number from ${settings.max_results.min} to ${settings.max_results.max}.`;
  const topK = whole(values.top_k, settings.top_k);
  if (topK === null) errors.top_k = `Top-k must be a whole number from ${settings.top_k.min} to ${settings.top_k.max}.`;
  if (!settings.models.includes(values.model)) errors.model = 'Choose a model from the list.';
  if (Object.keys(errors).length) return { errors };
  return { errors, body: { term, max_results: maxResults, top_k: topK, model: values.model } };
}

export function createForm(ctx) {
  let settings = null;
  let sending = false;
  let reading = null;
  const field = (name) => $(FIELDS[name]);

  function setEnabled(enabled) {
    $('def-fieldset').disabled = !enabled;
    $('def-submit').disabled = !enabled || sending;
  }

  function showErrors(errors) {
    for (const name of Object.keys(FIELDS)) {
      const text = errors[name] || '';
      message(`${FIELDS[name]}-error`, text, text ? 'bad' : '');
      if (text) field(name).setAttribute('aria-invalid', 'true');
      else field(name).removeAttribute('aria-invalid');
    }
  }

  function limit(input, limits) {
    input.min = String(limits.min);
    input.max = String(limits.max);
    if (!String(input.value ?? '').trim()) input.value = String(limits.default);
  }

  function apply(loaded) {
    settings = loaded;
    limit(field('max_results'), settings.max_results);
    limit(field('top_k'), settings.top_k);
    $('def-max-results-hint').textContent = `${settings.max_results.min}–${settings.max_results.max}; PubMed articles to read.`;
    $('def-top-k-hint').textContent = `${settings.top_k.min}–${settings.top_k.max}; evidence passages kept.`;
    const select = field('model');
    const chosen = select.value && settings.models.includes(select.value) ? select.value : settings.default_model;
    select.replaceChildren();
    for (const model of settings.models) {
      const option = document.createElement('option');
      option.value = model;
      option.textContent = model;
      option.selected = model === chosen;
      select.append(option);
    }
    select.value = settings.models.includes(chosen) ? chosen : (settings.models[0] ?? '');
    $('def-embedding').textContent = settings.embedding_model
      ? `Evidence is ranked with ${settings.embedding_model} embeddings.`
      : '';
    const notice = missingNotice(settings.missing || []);
    $('def-missing').hidden = !notice;
    $('def-missing-text').textContent = notice;
    setEnabled(!notice);
  }

  async function readSettings() {
    $('def-retry').hidden = true;
    message('def-msg', 'Loading the settings…');
    try {
      apply(await getSettings());
      message('def-msg');
    } catch (error) {
      settings = null;
      setEnabled(false);
      message('def-msg', `Could not load the settings: ${error.message}`, 'bad');
      $('def-retry').hidden = false;
    }
  }

  // One read at a time: showing the page again while one is under way waits for it.
  function load() {
    if (!reading) reading = readSettings().finally(() => { reading = null; });
    return reading;
  }

  const read = () => Object.fromEntries(Object.keys(FIELDS).map((name) => [name, field(name).value]));

  async function submit() {
    if (sending || !settings || (settings.missing || []).length) return null;
    const { errors, body } = validate(read(), settings);
    showErrors(errors);
    message('def-msg');
    if (!body) {
      field(Object.keys(errors)[0]).focus();
      return null;
    }
    sending = true;
    setEnabled(true);
    message('def-msg', 'Starting the run…');
    try {
      const run = await startRun(body);
      message('def-msg');
      field('term').value = '';
      ctx.navigate(runHref(run.run_id));
      return run;
    } catch (error) {
      message('def-msg', error.message, 'bad');
      // A key removed since the settings were read: show which one.
      if (error.status === 409) await load();
      return null;
    } finally {
      sending = false;
      if (settings) setEnabled(!(settings.missing || []).length);
    }
  }

  $('def-form').addEventListener('submit', (event) => {
    event.preventDefault();
    submit();
  });
  $('def-retry').addEventListener('click', () => load());

  return {
    load,
    submit,
    // Settings that failed to load are tried again when the page comes back.
    show() { return settings ? Promise.resolve() : load(); },
    get settings() { return settings; },
  };
}
