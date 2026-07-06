import { checkCoreHealth, closeSession, startSession } from '../lib/http.js';
import { smokeThresholds } from '../lib/thresholds.js';
import { connectTerminal } from '../lib/ws.js';

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: {
    ...smokeThresholds,
    ddm_ws_message_received: ['rate==1.00'],
  },
};

export default function () {
  checkCoreHealth();

  const session = startSession();
  try {
    connectTerminal(session, { closeMode: 'detach' });
  } finally {
    closeSession(session.sessionId);
  }
}
