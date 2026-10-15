const DEFAULT_BASE_URL = 'https://api.ddm.keiyam.me';

function trimTrailingSlash(value) {
  return String(value || '').replace(/\/+$/, '');
}

function numberEnv(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') {
    return fallback;
  }

  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    throw new Error(`${name} must be a number`);
  }
  return parsed;
}

function stringEnv(name, fallback = '') {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') {
    return fallback;
  }
  return String(raw);
}

export const config = {
  baseUrl: trimTrailingSlash(stringEnv('BASE_URL', DEFAULT_BASE_URL)),
  hostId: stringEnv('HOST_ID'),
  crId: stringEnv('CR_ID'),
  vus: numberEnv('K6_VUS', 5),
  duration: stringEnv('K6_DURATION', '2m'),
  rampUp: stringEnv('K6_RAMP_UP', '30s'),
  rampDown: stringEnv('K6_RAMP_DOWN', '30s'),
  sessionHoldSeconds: numberEnv('K6_SESSION_HOLD_SECONDS', 2),
  wsCommand: stringEnv('K6_WS_COMMAND', 'printf ddm-k6\\n'),
  listLimit: numberEnv('K6_LIST_LIMIT', 20),
  requestTimeout: stringEnv('K6_HTTP_TIMEOUT', '30s'),
};

if (!Number.isInteger(config.vus) || config.vus < 1 || config.vus > 40) {
  throw new Error('K6_VUS must be an integer between 1 and the guarded ceiling of 40');
}

export function requireSessionInputs() {
  const missing = [];
  if (!config.hostId) {
    missing.push('HOST_ID');
  }
  if (!config.crId) {
    missing.push('CR_ID');
  }

  if (missing.length > 0) {
    throw new Error(`Missing required SSH session test env vars: ${missing.join(', ')}`);
  }
}

export function url(path) {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }

  const normalizedPath = String(path || '').startsWith('/') ? path : `/${path}`;
  return `${config.baseUrl}${normalizedPath}`;
}

export function normalizeWebSocketUrl(websocketUrl) {
  if (!websocketUrl) {
    throw new Error('Missing websocketUrl');
  }

  if (/^wss?:\/\//i.test(websocketUrl)) {
    return websocketUrl;
  }

  if (/^https?:\/\//i.test(websocketUrl)) {
    return websocketUrl.replace(/^http/i, 'ws');
  }

  const base = config.baseUrl.replace(/^http/i, 'ws');
  const normalizedPath = String(websocketUrl).startsWith('/') ? websocketUrl : `/${websocketUrl}`;
  return `${base}${normalizedPath}`;
}
