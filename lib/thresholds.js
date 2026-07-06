export const smokeThresholds = {
  checks: ['rate==1.00'],
  http_req_failed: ['rate==0.00'],
  http_req_duration: ['p(95)<1000'],
  ddm_ws_connect_success: ['rate==1.00'],
};

export const loadThresholds = {
  checks: ['rate>0.99'],
  http_req_failed: ['rate<0.01'],
  http_req_duration: ['p(95)<1500'],
  ddm_session_start_duration: ['p(95)<2000'],
  ddm_ws_connect_success: ['rate>0.99'],
};

export const readOnlyThresholds = {
  checks: ['rate>0.99'],
  http_req_failed: ['rate<0.01'],
  http_req_duration: ['p(95)<1000'],
};

export const stressThresholds = {
  checks: ['rate>0.95'],
  http_req_failed: ['rate<0.05'],
  ddm_session_start_duration: ['p(95)<5000'],
  ddm_session_close_failed: ['count<5'],
};
