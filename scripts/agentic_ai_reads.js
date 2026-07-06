import { sleep } from 'k6';
import { config } from '../lib/config.js';
import { get, listItems, parseJson, checkStatus } from '../lib/http.js';
import { readOnlyThresholds } from '../lib/thresholds.js';

export const options = {
  scenarios: {
    agentic_ai_reads: {
      executor: 'constant-vus',
      vus: config.vus,
      duration: config.duration,
      gracefulStop: '15s',
    },
  },
  thresholds: readOnlyThresholds,
};

function firstId(items, fields) {
  if (!items || items.length === 0) {
    return '';
  }

  for (const field of fields) {
    if (items[0][field]) {
      return items[0][field];
    }
  }
  return '';
}

export default function () {
  checkStatus(get('/agentic-ai/health'), 'agentic health returns 200', [200]);

  const alertsResponse = get(`/alerts?limit=${config.listLimit}`);
  checkStatus(alertsResponse, 'alerts list returns 200', [200]);
  const alerts = listItems(parseJson(alertsResponse, {}), ['alerts']);
  const alertId = firstId(alerts, ['alertId', 'id']);
  if (alertId) {
    checkStatus(get(`/alerts/${encodeURIComponent(alertId)}`), 'alert detail returns 200', [200]);
  }

  const incidentsResponse = get(`/incidents?limit=${config.listLimit}`);
  checkStatus(incidentsResponse, 'incidents list returns 200', [200]);
  const incidents = listItems(parseJson(incidentsResponse, {}), ['incidents']);
  const incidentId = firstId(incidents, ['incidentId', 'id']);
  if (incidentId) {
    checkStatus(get(`/incidents/${encodeURIComponent(incidentId)}`), 'incident detail returns 200', [200]);
  }

  checkStatus(
    get(`/incidents/events?limit=${config.listLimit}`),
    'incident events returns 200',
    [200],
  );

  sleep(1);
}
