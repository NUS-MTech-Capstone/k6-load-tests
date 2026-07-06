function parseHeaderLine(line) {
  const value = String(line || '').trim();
  if (!value) {
    return {};
  }

  const colon = value.indexOf(':');
  if (colon === -1) {
    return { Cookie: value };
  }

  const name = value.slice(0, colon).trim();
  const headerValue = value.slice(colon + 1).trim();
  if (!name || !headerValue) {
    return {};
  }

  return { [name]: headerValue };
}

function parseJsonHeaders(raw) {
  if (!raw) {
    return {};
  }

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }
    return parsed;
  } catch (error) {
    console.warn(`Ignoring invalid DDM_AUTH_HEADERS_JSON: ${error.message}`);
    return {};
  }
}

export function authHeaders(extra = {}) {
  return {
    ...parseHeaderLine(__ENV.DDM_ALB_COOKIE_HEADER),
    ...parseJsonHeaders(__ENV.DDM_AUTH_HEADERS_JSON),
    ...extra,
  };
}

export function hasAuthHeaders() {
  return Object.keys(authHeaders()).length > 0;
}
