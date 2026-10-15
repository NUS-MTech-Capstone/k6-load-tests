export const smokeThresholds = {
  checks: ['rate==1.00'],
  'http_req_failed{surface:api}': ['rate==0.00'],
  'http_req_duration{surface:api}': ['p(95)<1000'],
  ddm_ws_connect_success: ['rate==1.00'],
  ddm_session_close_failed: ['count==0'],
};

export const loadThresholds = {
  checks: ['rate>0.99'],
  'http_req_failed{surface:api}': ['rate<0.01'],
  'http_req_duration{surface:api}': ['p(95)<1500'],
  ddm_session_start_duration: ['p(95)<2000'],
  ddm_ws_connect_success: ['rate>=0.99'],
  ddm_session_close_failed: ['count==0'],
};

export const readOnlyThresholds = {
  checks: ['rate>0.99'],
  'http_req_failed{surface:api}': ['rate<0.01'],
  'http_req_duration{surface:api}': ['p(50)<1000', 'p(95)<1000'],
};

export const stressThresholds = {
  checks: ['rate>=0.99'],
  'http_req_failed{surface:api}': ['rate<0.01'],
  ddm_session_start_duration: ['p(95)<2000'],
  ddm_session_close_failed: ['count==0'],
};
