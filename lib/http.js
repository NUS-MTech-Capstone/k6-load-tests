import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { authHeaders } from './auth.js';
import { config, requireSessionInputs, url } from './config.js';

export const sessionStartDuration = new Trend('ddm_session_start_duration', true);
export const sessionCloseFailures = new Counter('ddm_session_close_failed');

function params(extra = {}) {
  return {
    timeout: config.requestTimeout,
    ...extra,
    headers: {
      Accept: 'application/json',
      ...authHeaders(),
      ...(extra.headers || {}),
    },
  };
}

function jsonParams(extra = {}) {
  return params({
    ...extra,
    headers: {
      'Content-Type': 'application/json',
      ...(extra.headers || {}),
    },
  });
}

export function get(path, extra = {}) {
  return http.get(url(path), params(extra));
}

export function postJson(path, body, extra = {}) {
  return http.post(url(path), JSON.stringify(body || {}), jsonParams(extra));
}

export function checkStatus(response, label, statuses = [200]) {
  return check(response, {
    [label]: (r) => statuses.includes(r.status),
  });
}

export function parseJson(response, fallback = null) {
  try {
    return response.json();
  } catch (_) {
    return fallback;
  }
}

export function listItems(payload, fieldNames = []) {
  if (Array.isArray(payload)) {
    return payload;
  }

  if (!payload || typeof payload !== 'object') {
    return [];
  }

  const candidates = ['items', 'data', ...fieldNames];
  for (const field of candidates) {
    if (Array.isArray(payload[field])) {
      return payload[field];
    }
  }

  return [];
}

export function startSession() {
  requireSessionInputs();

  const response = postJson('/sessions/start', {
    hostId: config.hostId,
    crId: config.crId,
    connectionType: 'ssh',
  });
  sessionStartDuration.add(response.timings.duration);

  checkStatus(response, 'session start returns 200', [200]);
  const body = parseJson(response, {});
  check(body, {
    'session start returns sessionId': (b) => Boolean(b.sessionId),
    'session start returns sessionToken': (b) => Boolean(b.sessionToken),
    'session start returns websocketUrl': (b) => Boolean(b.websocketUrl),
  });

  return body;
}

export function rejoinSession(sessionId) {
  const response = postJson(`/sessions/${sessionId}/rejoin`, {});
  checkStatus(response, 'rejoin returns 200', [200]);
  return parseJson(response, {});
}

export function observeSession(sessionId) {
  const response = postJson(`/sessions/${sessionId}/observe`, {});
  checkStatus(response, 'observe returns 200', [200]);
  return parseJson(response, {});
}

export function closeSession(sessionId) {
  if (!sessionId) {
    return;
  }

  const response = postJson(`/sessions/${sessionId}/close`, {});
  const ok = response.status >= 200 && response.status < 300;
  if (!ok) {
    sessionCloseFailures.add(1);
  }
  check(response, {
    'session close returns 2xx': (r) => r.status >= 200 && r.status < 300,
  });
}

export function checkCoreHealth() {
  checkStatus(get('/healthz'), 'healthz returns 200', [200]);
  checkStatus(get('/readyz'), 'readyz returns 200', [200]);
  checkStatus(get('/agentic-ai/health'), 'agentic health returns 200', [200]);
}

export function checkSessionReadApis() {
  checkStatus(
    get(`/sessions?mine=true&status=ACTIVE&limit=${config.listLimit}`),
    'session list returns 200',
    [200],
  );
  checkStatus(
    get(`/sessions/rejoin-candidates?hostId=${encodeURIComponent(config.hostId)}&crId=${encodeURIComponent(config.crId)}`),
    'rejoin candidates returns 200',
    [200],
  );
}
