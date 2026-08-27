import { sleep } from 'k6';
import {
  checkCoreHealth,
  checkSessionReadApis,
  closeSession,
  observeSession,
  rejoinSession,
  startSession,
} from '../lib/http.js';
import { config } from '../lib/config.js';
import { loadThresholds } from '../lib/thresholds.js';
import { connectTerminal } from '../lib/ws.js';

export { handleSummary } from '../lib/summary.js';

export const options = {
  scenarios: {
    session_lifecycle: {
      executor: 'constant-vus',
      vus: config.vus,
      duration: config.duration,
      gracefulStop: '30s',
    },
  },
  thresholds: {
    ...loadThresholds,
    ddm_ws_message_received: ['rate>0.95'],
  },
};

export default function () {
  checkCoreHealth();

  const session = startSession();
  try {
    connectTerminal(session, { closeMode: 'detach' });
    checkSessionReadApis();

    sleep(1);

    const rejoin = rejoinSession(session.sessionId);
    connectTerminal(rejoin, {
      command: '',
      closeMode: 'detach',
      sendInput: false,
    });

    const observer = observeSession(session.sessionId);
    connectTerminal(observer, {
      command: '',
      closeMode: 'detach',
      sendInput: false,
      holdSeconds: Math.min(config.sessionHoldSeconds, 1),
    });
  } finally {
    closeSession(session.sessionId);
  }
}
