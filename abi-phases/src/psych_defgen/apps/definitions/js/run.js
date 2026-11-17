// One run: read again every 5 seconds while it is pending or running, then
// its error or its result, which can be downloaded as JSON.

import { getRun } from './api.js';
import { fileName, runBodyHtml, summaryHtml } from './result.js';
import { $, isActive, latest, message, poller } from './ui.js';

export const POLL_MS = 5000;

// Hands the browser a file to save, without a request: the result is already here.
export function saveJson(text, name) {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function createRunView(ctx) {
  const save = ctx.save || saveJson;
  let runId = '';
  let run = null;
  const token = latest();
  const poll = poller(() => load({ quiet: true }), { ms: POLL_MS, timers: ctx.timers });

  function render() {
    $('run-title').textContent = run ? run.term : 'Run';
    document.title = run ? `${run.term} · Construct Definitions` : 'Construct Definitions';
    $('run-summary').innerHTML = run ? summaryHtml(run) : '';
    $('run-body').innerHTML = run ? runBodyHtml(run) : '';
    $('run-download').hidden = !(run?.status === 'succeeded' && run.result);
  }

  async function load({ quiet = false } = {}) {
    poll.stop();
    const mine = token.next();
    if (!quiet) message('run-msg', 'Loading the run…');
    try {
      const data = await getRun(runId);
      if (!token.is(mine)) return;
      run = data;
      message('run-msg');
      render();
    } catch (error) {
      if (!token.is(mine)) return;
      if (error.status === 404) {
        run = null;
        render();
        $('run-title').textContent = 'Run not found';
        message('run-msg', 'This run does not exist. Go back to all runs and open another one.', 'bad');
        return;
      }
      message('run-msg', `Could not load the run: ${error.message}`, 'bad');
    }
    if (isActive(run)) poll.schedule();
  }

  function download() {
    if (!run?.result) return '';
    const name = fileName(run.result.term ?? run.term);
    save(JSON.stringify(run.result, null, 2), name);
    return name;
  }

  $('run-download').addEventListener('click', () => download());

  return {
    show(route) {
      if (route.runId !== runId) {
        runId = route.runId;
        run = null;
        render();
      }
      return load();
    },
    hide() { poll.stop(); token.next(); },
    pause() { poll.stop(); },
    refresh() { return load({ quiet: true }); },
    download,
    get polling() { return poll.active; },
    get run() { return run; },
  };
}
