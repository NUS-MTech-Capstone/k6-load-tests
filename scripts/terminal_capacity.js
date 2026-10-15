import ws from 'k6/ws';
import { sleep, check } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';
import { startSession, closeSession } from '../lib/http.js';
import { inputPacket, resizePacket, detachPacket } from '../lib/ws.js';
import { authHeaders } from '../lib/auth.js';
import { config, normalizeWebSocketUrl } from '../lib/config.js';
import { EchoProbe, PACKET, validWorkload } from '../lib/echo.mjs';
import { coordinate } from '../lib/campaign.js';
export { handleSummary } from '../lib/summary.js';

if (!validWorkload(config.vus)) throw new Error('Terminal capacity requires 1–40 integral VUs');
const seconds = Number(__ENV.K6_MEASURE_SECONDS || 300);
const ramp = Number(__ENV.K6_RAMP_SECONDS || 60);
if (!(seconds > 0 && seconds <= 1200 && ramp >= 0 && ramp <= 60)) throw new Error('Invalid bounded duration');
const readiness = new Trend('ddm_ssh_ready_ms', true);
const echo = new Trend('ddm_terminal_echo_ms', true);
const probes = new Rate('ddm_terminal_probe_success');
const connects = new Rate('ddm_ws_connect_success');
const unexpected = new Counter('ddm_ws_unexpected_disconnects');
const timeouts = new Counter('ddm_terminal_probe_timeouts');
const interruptedProbes = new Counter('ddm_terminal_probe_disconnects');
const protocolErrors = new Counter('ddm_terminal_protocol_errors');

export const options = {
  scenarios: { terminal_capacity: { executor: 'per-vu-iterations', vus: config.vus, iterations: 1, maxDuration: `${ramp + seconds + 60}s` } },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(50)', 'p(95)', 'p(99)', 'count'],
  thresholds: {
    'http_req_failed{surface:api}': ['rate<0.01'],
    ddm_session_start_duration: ['p(95)<2000'], ddm_ws_connect_success: ['rate>=0.99'],
    'ddm_terminal_echo_ms{phase:steady}': ['p(95)<200'],
    'ddm_terminal_probe_success{phase:steady}': ['rate>=0.99'],
    ddm_session_close_failed: ['count==0'], ddm_ws_unexpected_disconnects: ['count==0'],
    ddm_terminal_protocol_errors: ['count==0'],
  },
};

export default function () {
  // VUs begin together; one staggered start each avoids churn during the plateau.
  const origin = Date.now();
  sleep((__VU - 1) * ramp / config.vus);
  let steadyAt = __ENV.K6_COORDINATOR_URL ? Infinity : origin + ramp * 1000;
  let endAt = steadyAt + seconds * 1000;
  let ready = false;
  let connectAt = 0;
  let session;
  let opened = false;
  let intentional = false;
  let recordedClose = false;
  let sequence = 0;
  let phase = 'ramp';
  const probe = new EchoProbe();
  unexpected.add(0); timeouts.add(0); interruptedProbes.add(0); protocolErrors.add(0);
  function record(result) {
    if (!result) return;
    probes.add(result.ok, { phase });
    if (result.ok) {
      echo.add(result.latency, { phase });
      if (!ready) {
        ready = true; readiness.add(Date.now() - connectAt);
        coordinate('event', { kind: 'ready', sessionId: session.sessionId, vu: __VU });
      }
    }
    else if (result.reason === 'timeout') timeouts.add(1);
    else if (result.reason === 'disconnect') interruptedProbes.add(1);
    coordinate('event', { kind: 'probe', vu: __VU, ok: result.ok, latency: result.latency, reason: result.reason, phase });
  }
  try {
    session = startSession();
    connectAt = Date.now();
    const response = ws.connect(normalizeWebSocketUrl(session.websocketUrl), {
      headers: { Origin: config.baseUrl, 'x-api-version': '1', ...authHeaders(), Authorization: `Bearer ${session.sessionToken}` },
      tags: { name: '/sessions/:id/ws', surface: 'terminal' },
    }, socket => {
      socket.on('open', () => {
        opened = true; coordinate('event', { kind: 'opened', sessionId: session.sessionId, vu: __VU });
        socket.sendBinary(resizePacket());
        socket.setInterval(() => {
          record(probe.expire(Date.now()));
          if (probe.pending || Date.now() + probe.timeoutMs >= endAt) return;
          phase = Date.now() >= steadyAt ? 'steady' : 'ramp';
          socket.sendBinary(inputPacket('\u0015'));
          const marker = `ddmk6_${__ENV.K6_RUN_NONCE || 'local'}_${__VU}_${++sequence}`;
          probe.begin(marker, Date.now()); socket.sendBinary(inputPacket(marker));
        }, 2000);
        socket.setInterval(() => {
          record(probe.expire(Date.now()));
          if (__ENV.K6_COORDINATOR_URL && !Number.isFinite(steadyAt)) {
            const state = coordinate('state');
            if (state?.plateauStartMs) { steadyAt = state.plateauStartMs; endAt = state.plateauEndMs; }
            if (state?.stop) socket.close();
          }
          if (Date.now() >= endAt && !intentional) {
            intentional = true;
            socket.sendBinary(inputPacket('\u0015')); socket.sendBinary(detachPacket()); socket.close();
          }
        }, 200);
        // A barrier cannot wait forever for a missing or unusable session.
        socket.setTimeout(() => { if (!intentional) socket.close(); }, (ramp + seconds + 60) * 1000);
      });
      socket.on('binaryMessage', packet => {
        if (new Uint8Array(packet)[0] === PACKET.error) {
          protocolErrors.add(1); record(probe.fail('protocol_error'));
          coordinate('event', { kind: 'ws_error', vu: __VU });
        } else record(probe.receive(packet, Date.now()));
      });
      socket.on('close', () => {
        if (!intentional) unexpected.add(1);
        record(probe.fail('disconnect'));
        coordinate('event', { kind: 'disconnected', sessionId: session.sessionId, vu: __VU, unexpected: !intentional });
        recordedClose = true;
      });
      socket.on('error', () => coordinate('event', { kind: 'ws_error', vu: __VU }));
    });
    connects.add(opened && response?.status === 101);
    check(opened, { 'terminal connected': x => x });
  } finally {
    if (opened && !recordedClose) coordinate('event', { kind: 'disconnected', sessionId: session.sessionId, vu: __VU, unexpected: true });
    if (session?.sessionId) closeSession(session.sessionId);
  }
}
