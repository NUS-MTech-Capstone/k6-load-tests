import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { authHeaders } from './auth.js';
import { config, requireSessionInputs, url } from './config.js';
import { coordinate } from './campaign.js';
import { fail } from 'k6';

export const sessionStartDuration = new Trend('ddm_session_start_duration', true);
export const sessionCloseFailures = new Counter('ddm_session_close_failed');
export const apiRequests = new Counter('ddm_api_requests');

function params(extra = {}) {
  return {
    timeout: config.requestTimeout,
    redirects: 0,
    tags: { surface: 'api' },
    ...extra,
    headers: {
      Accept: 'application/json',
      'x-api-version': '1',
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
  const r = http.get(url(path), params({ ...extra, tags: { surface: 'api', name: path.split('?')[0].replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/g, ':id').replace(/inc-[0-9a-f]+/g, ':id') } }));
  apiRequests.add(1);
  if ([401, 403].includes(r.status)) coordinate('event', { kind: 'authorization_failure', status: r.status });
  return r;
}

export function postJson(path, body, extra = {}) {
  const response = http.post(url(path), JSON.stringify(body || {}), jsonParams({ ...extra, tags: { surface: 'api', name: path.replace(/\/sessions\/[^/]+\//, '/sessions/:id/') } }));
  apiRequests.add(1);
  if ([401, 403].includes(response.status)) coordinate('event', { kind: 'authorization_failure', status: response.status });
  return response;
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
  coordinate('permit');
  sessionCloseFailures.add(0);

  const response = postJson('/sessions/start', {
    hostId: config.hostId,
    crId: config.crId,
    connectionType: 'ssh',
  });
  sessionStartDuration.add(response.timings.duration);

  checkStatus(response, 'session start returns 200', [200]);
  const body = parseJson(response, {});
  if (body.sessionId) coordinate('registered', { sessionId: body.sessionId });
  if (response.status !== 200 || !body.sessionId || !body.sessionToken || !body.websocketUrl) {
    coordinate('event', { kind: 'start_failure', status: response.status });
    fail('Session start failed; response content omitted');
  }
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
  coordinate('closed', { sessionId, ok });
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
