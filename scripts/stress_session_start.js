import { sleep } from 'k6';
import { closeSession, startSession } from '../lib/http.js';
import { config } from '../lib/config.js';
import { stressThresholds } from '../lib/thresholds.js';

export { handleSummary } from '../lib/summary.js';

export const options = {
  scenarios: {
    stress_session_start: {
      executor: 'ramping-vus',
      stages: [
        { duration: config.rampUp, target: config.vus },
        { duration: config.duration, target: config.vus },
        { duration: config.rampDown, target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: stressThresholds,
};

export default function () {
  const session = startSession();
  try {
    sleep(0.2);
  } finally {
    closeSession(session.sessionId);
  }
}
