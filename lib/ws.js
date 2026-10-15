import ws from 'k6/ws';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';
import { authHeaders } from './auth.js';
import { config, normalizeWebSocketUrl } from './config.js';
import { PACKET } from './echo.mjs';

export const wsConnectSuccess = new Rate('ddm_ws_connect_success');
export const wsMessageReceived = new Rate('ddm_ws_message_received');
export const wsErrorCount = new Counter('ddm_ws_errors');

function asciiPacket(type, text = '') {
  const bytes = new Uint8Array(1 + text.length);
  bytes[0] = type;
  for (let i = 0; i < text.length; i += 1) {
    bytes[i + 1] = text.charCodeAt(i) & 0xff;
  }
  return bytes.buffer;
}

function singleBytePacket(type) {
  const bytes = new Uint8Array(1);
  bytes[0] = type;
  return bytes.buffer;
}

export function resizePacket(columns = 120, rows = 40) {
  const bytes = new Uint8Array(5);
  bytes[0] = PACKET.resize;
  bytes[1] = (columns >> 8) & 0xff;
  bytes[2] = columns & 0xff;
  bytes[3] = (rows >> 8) & 0xff;
  bytes[4] = rows & 0xff;
  return bytes.buffer;
}

export function inputPacket(text) {
  return asciiPacket(PACKET.input, text);
}

export function detachPacket() {
  return singleBytePacket(PACKET.detach);
}

export function terminatePacket() {
  return singleBytePacket(PACKET.terminate);
}

export function connectTerminal(session, options = {}) {
  const targetUrl = normalizeWebSocketUrl(session.websocketUrl);
  const holdMs = Math.max(0.1, options.holdSeconds || config.sessionHoldSeconds) * 1000;
  const sendInput = options.sendInput !== false;
  const command = options.command === undefined ? config.wsCommand : options.command;
  const closeMode = options.closeMode || 'detach';

  let opened = false;
  let received = false;

  const response = ws.connect(targetUrl, {
    headers: {
      'x-api-version': '1',
      ...authHeaders(),
      Authorization: `Bearer ${session.sessionToken}`,
    },
  }, (socket) => {
    socket.on('open', () => {
      opened = true;
      socket.sendBinary(resizePacket());
      if (sendInput && command) {
        socket.sendBinary(inputPacket(command));
      }

      socket.setTimeout(() => {
        if (closeMode === 'terminate') {
          socket.sendBinary(terminatePacket());
        } else if (closeMode === 'detach') {
          socket.sendBinary(detachPacket());
        }
        socket.close();
      }, holdMs);
    });

    socket.on('binaryMessage', (message) => {
      if (!message || message.byteLength < 1) {
        return;
      }

      const packetType = new Uint8Array(message)[0];
      if ([PACKET.output, PACKET.error, PACKET.event].includes(packetType)) {
        received = true;
      }
    });

    socket.on('message', () => {
      received = true;
    });

    socket.on('error', () => {
      wsErrorCount.add(1);
    });
  });

  const connected = response && response.status === 101 && opened;
  wsConnectSuccess.add(connected);
  wsMessageReceived.add(received);

  check(response, {
    'websocket upgrade returns 101': (r) => r && r.status === 101,
  });
  check({ opened, received }, {
    'websocket opens': (r) => r.opened,
    'websocket receives output or event': (r) => r.received,
  });

  return { connected, received };
}
