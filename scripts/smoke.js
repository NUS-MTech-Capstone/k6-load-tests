import { checkCoreHealth } from '../lib/http.js';
import { lifecycle } from '../lib/lifecycle.js';
export { handleSummary } from '../lib/summary.js';
export const options = { vus: 1, iterations: 1, thresholds: {
  checks: ['rate==1'], 'http_req_failed{surface:api}': ['rate==0'],
  ddm_lifecycle_success: ['rate==1'], ddm_lifecycle_connect_success: ['rate==1'],
  ddm_session_close_failed: ['count==0'], ddm_lifecycle_errors: ['count==0'],
}};
export default function () { checkCoreHealth(); lifecycle(); }
