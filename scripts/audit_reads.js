import { sleep } from 'k6';
import { config } from '../lib/config.js';
import { checkStatus, get, listItems, parseJson } from '../lib/http.js';
import { readOnlyThresholds } from '../lib/thresholds.js';

export const options = {
  scenarios: {
    audit_reads: {
      executor: 'constant-vus',
      vus: config.vus,
      duration: config.duration,
      gracefulStop: '15s',
    },
  },
  thresholds: readOnlyThresholds,
};

function firstSessionId(payload) {
  const items = listItems(payload, ['sessions']);
  if (!items.length) {
    return '';
  }
  return items[0].sessionId || items[0].id || '';
}

export default function () {
  const sessionsResponse = get(`/ddm/session-audit/sessions?limit=${config.listLimit}`);
  checkStatus(sessionsResponse, 'session audit sessions returns 200', [200]);

  const sessionId = firstSessionId(parseJson(sessionsResponse, {}));
  if (sessionId) {
    checkStatus(
      get(`/ddm/session-audit/sessions/${encodeURIComponent(sessionId)}/records?limit=${config.listLimit}`),
      'session audit records by session returns 200',
      [200],
    );
  }

  checkStatus(
    get(`/ddm/session-audit/records?limit=${config.listLimit}`),
    'session audit records returns 200',
    [200],
  );

  checkStatus(
    get(`/ddm/audit/events?limit=${config.listLimit}`),
    'audit events returns 200',
    [200],
  );

  sleep(1);
}
