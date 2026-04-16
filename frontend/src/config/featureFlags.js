// Application-wide feature flags
// These control visibility of entire feature sections (navigation, routes, UI).
// Set via environment variables at build time (Vite: VITE_*).
// All default to true — set to 'false' to hide a section entirely.

const parseBoolean = (envKey, fallback = false) => {
  const raw = import.meta.env[envKey];
  if (raw === undefined || raw === null || raw === '') {
    return fallback;
  }
  const val = String(raw).trim().toLowerCase();
  if (val === 'true') {
    return true;
  }
  if (val === 'false') {
    return false;
  }
  return fallback;
};

export const PLUGINS_ENABLED = parseBoolean('VITE_PLUGINS_ENABLED', true);
export const MCP_ENABLED = parseBoolean('VITE_MCP_ENABLED', true);
export const EXPERIENCES_ENABLED = parseBoolean('VITE_EXPERIENCES_ENABLED', true);
