// The app: two views chosen by the hash route. #/ shows the form and the
// history, #/runs/<run_id> one run. Only the shown view polls, and nothing
// polls while the page is hidden.

import { createForm } from './form.js';
import { createHistory } from './history.js';
import { createRouter, homeHref } from './router.js';
import { createRunView } from './run.js';
import { $, TIMERS } from './ui.js';

// Follow the Nexus light/dark choice when the embedding page exposes it.
function syncTheme() {
  try {
    if (window.parent === window) return;
    const root = window.parent.document.documentElement;
    const apply = () => {
      const theme = root.classList.contains('dark') ? 'dark' : root.classList.contains('light') ? 'light' : '';
      if (theme) document.documentElement.dataset.theme = theme;
      else delete document.documentElement.dataset.theme;
    };
    apply();
    new MutationObserver(apply).observe(root, { attributes: true, attributeFilter: ['class'] });
  } catch (_crossOrigin) {
    // Served on its own: the system colour scheme applies.
  }
}

export function boot({ timers = TIMERS, save } = {}) {
  let router;
  const ctx = { timers, save, navigate: (target) => router.navigate(target) };
  const form = createForm(ctx);
  const history = createHistory(ctx);
  const runView = createRunView(ctx);
  let active = '';

  const home = {
    show() { form.show(); return history.show(); },
    hide() { history.hide(); },
    pause() { history.pause(); },
    refresh() { form.show(); return history.refresh(); },
  };
  const views = { home, run: runView };

  function show(route) {
    const entering = route.name !== active;
    if (entering && active) views[active].hide();
    $('view-home').hidden = route.name !== 'home';
    $('view-run').hidden = route.name !== 'run';
    if (route.name === 'home') document.title = 'Construct Definitions';
    active = route.name;
    views[active].show(route);
    if (entering) (route.name === 'run' ? $('run-title') : $('app-title')).focus?.();
  }

  document.addEventListener('visibilitychange', () => {
    if (!active) return;
    if (document.hidden) views[active].pause();
    else views[active].refresh();
  });
  $('run-back').href = homeHref();
  syncTheme();
  router = createRouter(show);
  router.start();
  return { ctx, form, history, runView, views, router, get active() { return active; } };
}
