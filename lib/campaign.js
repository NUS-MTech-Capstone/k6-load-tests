import http from 'k6/http';
import { fail } from 'k6';

export function coordinate(kind, fields = {}) {
  if (!__ENV.K6_COORDINATOR_URL) return;
  const result = http.post(`${__ENV.K6_COORDINATOR_URL}/${kind}`, JSON.stringify(fields), {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${__ENV.K6_COORDINATOR_TOKEN}` },
    tags: { surface: 'coordination', name: `coordination/${kind}` }, timeout: '60s', redirects: 0,
  });
  if (result.status !== 200) fail('Campaign coordinator denied request');
  return result.json();
}
