import ws from 'k6/ws';
import { check, fail } from 'k6';
import { Rate, Counter } from 'k6/metrics';
import { authHeaders } from './auth.js';
import { config, normalizeWebSocketUrl } from './config.js';
import { inputPacket, resizePacket, detachPacket, terminatePacket } from './ws.js';
import { PACKET } from './echo.mjs';
import { coordinate } from './campaign.js';
import { startSession, rejoinSession, observeSession, closeSession, get } from './http.js';

const outcomes = new Rate('ddm_lifecycle_success');
const connects = new Rate('ddm_lifecycle_connect_success');
const errors = new Counter('ddm_lifecycle_errors');

function attach(session, { marker, previous = '', observer = false, terminate = false }) {
  let opened = false, echo = false, retained = !previous, denied = false, unexpected = false;
  let intentional = false, buffer = '', observationDone = false;
  const started = Date.now();
  const r = ws.connect(normalizeWebSocketUrl(session.websocketUrl), {
    headers: { Origin: config.baseUrl, 'x-api-version': '1', ...authHeaders(), Authorization: `Bearer ${session.sessionToken}` },
    tags: { surface: 'terminal', name: '/sessions/:id/ws' },
  }, socket => {
    socket.on('open', () => {
      opened = true;
      socket.sendBinary(resizePacket(100, 32));
      // Never submit a command. The observer deliberately attempts this same harmless input.
      if (!observer) socket.sendBinary(inputPacket('\u0015'));
      socket.sendBinary(inputPacket(marker));
      socket.setTimeout(() => {
        observationDone = true; intentional = true;
        if (!observer) socket.sendBinary(inputPacket('\u0015'));
        socket.sendBinary(terminate ? terminatePacket() : detachPacket());
        // Allow the server to acknowledge termination by closing the socket.
        if (!terminate) socket.close();
      }, 1700);
      socket.setTimeout(() => socket.close(), 2500);
    });
    socket.on('binaryMessage', data => {
      const bytes = new Uint8Array(data);
      if (bytes[0] === PACKET.error) {
        const message = Array.from(bytes.slice(1), b => String.fromCharCode(b)).join('');
        if (observer && message === 'attachment is not the active session writer') denied = true;
        else unexpected = true;
      }
      if (bytes[0] === PACKET.output) {
        buffer = (buffer + Array.from(bytes.slice(1), b => String.fromCharCode(b)).join('')).slice(-4096);
        if (buffer.includes(marker)) echo = true;
        if (previous && buffer.includes(previous)) retained = true;
      }
    });
    socket.on('error', () => { unexpected = true; });
    socket.on('close', () => { if (!intentional) unexpected = true; });
  });
  const connected = opened && r?.status === 101;
  const ok = connected && observationDone && !unexpected && retained && (observer ? denied && !echo : echo);
  connects.add(connected); outcomes.add(ok); errors.add(ok ? 0 : 1);
  check(ok, { [observer ? 'observer input rejected without echo' : previous ? 'rejoin retains output and accepts new echo' : 'owner echoes after resize']: v => v });
  coordinate('event', { kind: 'lifecycle', sessionId: session.sessionId, ok, reason: observer ? 'observe_read_only' : previous ? 'rejoin' : 'owner', latency: Date.now() - started });
  if (!ok) { coordinate('event', { kind: 'ws_error' }); fail('Lifecycle assertion failed; terminal content omitted'); }
}

export function lifecycle() {
  const s = startSession();
  const nonce = `${__ENV.K6_RUN_NONCE || 'local'}_${__VU}_${__ITER}`;
  const first = `ddmfirst_${nonce}`;
  try {
    attach(s, { marker: first });
    attach(rejoinSession(s.sessionId), { marker: `ddmrejoin_${nonce}`, previous: first });
    attach(observeSession(s.sessionId), { marker: `ddmdenied_${nonce}`, observer: true });
    attach(rejoinSession(s.sessionId), { marker: `ddmend_${nonce}`, terminate: true });
  } finally { closeSession(s.sessionId); }
}
