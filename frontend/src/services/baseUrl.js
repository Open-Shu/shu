import { log } from '../utils/log';

// Derived API base: prefer explicit env; else use same-origin.
// Note: No hardcoded port; set REACT_APP_API_BASE_URL to control host:port.
export function getApiBaseUrl() {
  const explicit = process.env.REACT_APP_API_BASE_URL; // e.g., "http://localhost:8000" or "https://shu.mxw.ai"
  if (explicit) {
    return explicit;
  }

  // Warn in local dev if running on :3000 without explicit API base, since WS may not proxy correctly
  try {
    const u = new URL(window.location.origin);
    if ((u.hostname === 'localhost' || u.hostname === '127.0.0.1') && u.port === '3000') {
      // Visible warning preferred by project standards
      // eslint-disable-next-line no-console
      log.warn(
        '[Shu] REACT_APP_API_BASE_URL not set; using same-origin (localhost:3000). If WebSocket upgrades fail, set REACT_APP_API_BASE_URL=http://localhost:8000'
      );
    }
    return `${u.protocol}//${u.host}`;
  } catch (e) {
    return window.location.origin;
  }
}

export function getApiV1Base() {
  const base = getApiBaseUrl();
  return `${base.replace(/\/$/, '')}/api/v1`;
}

export function getWsBaseUrl() {
  // Base this on the API base so we don’t accidentally target the frontend host/port
  const apiBase = getApiBaseUrl();
  try {
    const url = new URL(apiBase);
    const wsProtocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProtocol}//${url.host}`;
  } catch (e) {
    // Fallback to same-origin
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}`;
  }
}
