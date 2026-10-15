import { lifecycle } from '../lib/lifecycle.js';
import { config } from '../lib/config.js';
export { handleSummary } from '../lib/summary.js';
export const options = {
  scenarios: { session_lifecycle: { executor: 'constant-vus', vus: config.vus, duration: config.duration, gracefulStop: '30s' } },
  thresholds: { checks: ['rate>=0.99'], 'http_req_failed{surface:api}': ['rate<0.01'],
    ddm_session_start_duration: ['p(95)<2000'], ddm_lifecycle_success: ['rate==1'],
    ddm_lifecycle_connect_success: ['rate>=0.99'], ddm_session_close_failed: ['count==0'], ddm_lifecycle_errors: ['count==0'] },
};
export default function () { lifecycle(); }
