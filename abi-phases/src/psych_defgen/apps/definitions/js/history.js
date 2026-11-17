// The history of runs, newest first, a page at a time. While a run on the
// shown page is pending or running, the page is read again every 5 seconds.

import { listRuns } from './api.js';
import { runHref } from './router.js';
import { $, esc, formatDate, isActive, latest, message, poller, runDuration, stageLabel, statusPill } from './ui.js';

export const POLL_MS = 5000;

export function historyRow(run, now = Date.now()) {
  const stage = stageLabel(run);
  return `<tr>`
    + `<td data-label="Term"><a class="row-link" href="${esc(runHref(run.run_id))}">${esc(run.term)}</a></td>`
    + `<td data-label="Status"><span>${statusPill(run.status)}${stage ? `<small>${esc(stage)}</small>` : ''}</span></td>`
    + `<td data-label="Model">${esc(run.model || '—')}</td>`
    + `<td data-label="Started">${esc(formatDate(run.created_at))}</td>`
    + `<td data-label="Duration">${esc(runDuration(run, now))}</td>`
    + `</tr>`;
}

export function createHistory(ctx) {
  let page = 1;
  let pageCount = 0;
  let runs = [];
  let loading = false;
  const token = latest();
  const poll = poller(() => load(page, { quiet: true }), { ms: POLL_MS, timers: ctx.timers });

  function render() {
    const body = $('history-body');
    body.innerHTML = runs.length
      ? runs.map((run) => historyRow(run)).join('')
      : `<tr><td colspan="5" class="empty">${page > 1 ? 'No runs on this page.' : 'No runs yet. Start one with the form above.'}</td></tr>`;
    $('history-page').textContent = pageCount > 0 ? `Page ${page} of ${pageCount}` : 'Page 1 of 1';
    $('history-prev').disabled = loading || page <= 1;
    $('history-next').disabled = loading || page >= pageCount;
    $('history-pager').hidden = pageCount <= 1 && page <= 1;
  }

  async function load(target = page, { quiet = false } = {}) {
    poll.stop();
    const mine = token.next();
    loading = true;
    $('history-refresh').disabled = true;
    if (!quiet) message('history-msg', 'Loading runs…');
    try {
      const data = await listRuns(target);
      if (!token.is(mine)) return;
      page = Number(data.page) || target;
      pageCount = Number(data.page_count) || 0;
      runs = Array.isArray(data.runs) ? data.runs : [];
      message('history-msg');
    } catch (error) {
      if (!token.is(mine)) return;
      message('history-msg', `Could not load the runs: ${error.message}`, 'bad');
    } finally {
      if (token.is(mine)) {
        loading = false;
        $('history-refresh').disabled = false;
        render();
        // A failed read keeps polling only if work was in progress: it may be a blip.
        if (runs.some(isActive)) poll.schedule();
      }
    }
  }

  $('history-refresh').addEventListener('click', () => load(page));
  $('history-prev').addEventListener('click', () => load(Math.max(1, page - 1)));
  $('history-next').addEventListener('click', () => load(page + 1));

  return {
    load,
    show() { return load(page); },
    hide() { poll.stop(); token.next(); },
    pause() { poll.stop(); },
    refresh() { return load(page, { quiet: true }); },
    get polling() { return poll.active; },
    get page() { return page; },
  };
}
