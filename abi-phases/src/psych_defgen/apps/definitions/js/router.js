// Hash routes: #/ for the form and the history, #/runs/<run_id> for one run.
//
// Nexus mints a fresh iframe URL (with a new access token) whenever it reloads
// the app, which drops the hash. The last route is therefore kept in
// sessionStorage and restored when the app opens without one.

const STORAGE_KEY = 'healthyphases.definitions.route';

const decode = (part) => {
  try { return decodeURIComponent(part); } catch (_malformed) { return part; }
};

export function parse(hash) {
  const path = String(hash || '').replace(/^#\/?/, '').split('?')[0];
  const parts = path.split('/').filter(Boolean).map(decode);
  if (parts[0] === 'runs' && parts[1]) return { name: 'run', runId: parts[1] };
  return { name: 'home' };
}

export const homeHref = () => '#/';
export const runHref = (runId) => `#/runs/${encodeURIComponent(runId)}`;

function remember(hash) {
  try { sessionStorage.setItem(STORAGE_KEY, hash); } catch (_unavailable) { /* private mode: deep links still work */ }
}

function recalled() {
  try { return sessionStorage.getItem(STORAGE_KEY) || ''; } catch (_unavailable) { return ''; }
}

export function createRouter(onChange) {
  const current = () => parse(location.hash);

  function changed() {
    if (location.hash) remember(location.hash);
    onChange(current());
  }

  return {
    current,
    start() {
      const saved = recalled();
      if (!location.hash && saved) history.replaceState(null, '', saved);
      window.addEventListener('hashchange', changed);
      changed();
    },
    // Navigating to the route already shown re-renders it, like following a link again.
    navigate(target) {
      if (location.hash === target) changed();
      else location.hash = target;
    },
  };
}
