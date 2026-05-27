// Application-wide feature flags
// These control visibility of entire feature sections (navigation, routes, UI).
// Set via environment variables at build time (Vite: VITE_*).
// All default to true — set to 'false' to hide a section entirely.

import { useEntitlement } from '../contexts/BillingStatusContext';

const parseBoolean = (envKey, fallback = true) => {
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

// Single source of truth for feature gating. Each entry pairs the build-time
// env flag with the runtime entitlement key (from the billing API). To gate a
// new feature, add one row here and reference its key via `useFeatureEnabled`.
// `env: true` means "entitlement-only" (no build-time flag).
const FEATURE_GATES = {
  plugins: { env: PLUGINS_ENABLED, entitlement: 'plugins' },
  mcp: { env: MCP_ENABLED, entitlement: 'mcp_servers' },
  experiences: { env: EXPERIENCES_ENABLED, entitlement: 'experiences' },
  providers: { env: true, entitlement: 'provider_management' },
  modelConfigs: { env: true, entitlement: 'model_config_management' },
};

/**
 * useFeatureEnabled(feature) — true only when the build-time flag AND the
 * tenant's entitlement both allow the feature. Entitlements are unknown on
 * self-hosted (and before the first billing poll), where useEntitlement returns
 * true, so those deployments see every feature. Unknown feature keys are
 * treated as ungated (true) — a dev-time wiring mistake shouldn't hide UI.
 */
export const useFeatureEnabled = (feature) => {
  const gate = FEATURE_GATES[feature];
  // Hook must run unconditionally; the mapped key (or undefined) is fine.
  const entitled = useEntitlement(gate?.entitlement);
  return gate ? gate.env && entitled : true;
};
