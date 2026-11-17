// The client for /psych_defgen/api, and the only module that calls fetch.
//
// Nexus serves this page from /app-html/ inside an iframe, so a relative fetch
// would reach the Nexus frontend and come back as its HTML 404 page. The ABI
// API origin comes from the runtime config Nexus inlines into the embedding
// document instead; the iframe is same-origin, so the parent is readable.

export const PREFIX = '/psych_defgen/api';
export const PAGE_SIZE = 25;

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function apiBase() {
  const read = (w) => {
    try { return w?.__NEXUS_RUNTIME_CONFIG__?.apiUrl || ''; } catch (_crossOrigin) { return ''; }
  };
  const win = typeof window === 'undefined' ? undefined : window;
  return (read(win) || read(win?.parent) || read(win?.top)).replace(/\/$/, '');
}

export function authHeaders() {
  try {
    const token = JSON.parse(localStorage.getItem('nexus-auth') || '{}').state?.token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch (_unavailable) {
    return {};
  }
}

const STATUS_MESSAGES = {
  401: 'Your session has expired. Reload Nexus and sign in again.',
  403: 'Your account does not have access to this action. Ask a workspace admin for access.',
  404: 'This item no longer exists. Refresh the page and try again.',
  409: 'This deployment is not configured to produce definitions yet. Ask an administrator.',
  422: 'Some of the values entered are not valid. Check the form and try again.',
  502: 'An upstream service did not respond. Try again in a moment.',
  503: 'The service is temporarily unavailable. Try again in a moment.',
  504: 'The service took too long to respond. Try again in a moment.',
};

export function statusMessage(status) {
  return STATUS_MESSAGES[status] || `The request failed (${status}). Try again, or reload the app.`;
}

// FastAPI reports validation failures as a list of {loc, msg}; show the fields, not the JSON.
export function errorMessage(payload, status) {
  const detail = payload?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => {
      if (!item || typeof item !== 'object') return String(item);
      const field = (item.loc || []).filter((part) => part !== 'body' && part !== 'query').join('.');
      return field ? `${field}: ${item.msg}` : String(item.msg || '');
    }).join('; ');
  }
  return statusMessage(status);
}

export async function readJson(response, url) {
  const body = await response.text();
  try {
    return JSON.parse(body);
  } catch (_notJson) {
    // Almost always the request reached the wrong server; never show its HTML.
    throw new ApiError(
      `Expected JSON from ${url} but got ${response.status}. Check the ABI API connection (${apiBase() || 'same origin'}).`,
      response.status,
    );
  }
}

export async function request(path, { method = 'GET', body, signal } = {}) {
  const url = `${apiBase()}${PREFIX}${path}`;
  const headers = { ...authHeaders() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const init = { method, credentials: 'include', headers };
  if (body !== undefined) init.body = JSON.stringify(body);
  if (signal) init.signal = signal;
  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw new ApiError(error?.message ? `Could not reach the server: ${error.message}` : 'Could not reach the server. Check your connection and try again.');
  }
  const payload = await readJson(response, url);
  if (!response.ok) throw new ApiError(errorMessage(payload, response.status), response.status);
  return payload;
}

export const getSettings = () => request('/settings');

export const startRun = (body) => request('/runs', { method: 'POST', body });

export const listRuns = (page = 1, pageSize = PAGE_SIZE) => request(`/runs?page=${encodeURIComponent(page)}&page_size=${encodeURIComponent(pageSize)}`);

export const getRun = (runId) => request(`/runs/${encodeURIComponent(runId)}`);
