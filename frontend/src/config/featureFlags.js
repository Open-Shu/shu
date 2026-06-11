// Application-wide feature flags
// These control visibility of entire feature sections (navigation, routes, UI).
// Set via environment variables at build time (Vite: VITE_*).
// All default to true — set to 'false' to hide a section entirely.

import { useBillingStatus } from '../contexts/BillingStatusContext';

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

// Shu Assistant (how-to chat) entry in the Contact Support dialog. Defaults OFF:
// the seeded assistant model config does not exist yet, so showing it would drop
// users into an empty chat. Flip to 'true' once the assistant ships (SHU-857 follow-up).
export const SHU_ASSISTANT_ENABLED = parseBoolean('VITE_SHU_ASSISTANT_ENABLED', false);

// Single source of truth for feature gating. Each entry pairs the build-time
// env flag with the runtime entitlement keys (from the billing API). To gate a
// new feature, add one row here and reference its key via `useFeatureEnabled`.
// `env: true` means "entitlement-only" (no build-time flag); `entitlements` may
// list more than one key — every one must be granted for the feature to show.
const FEATURE_GATES = {
  plugins: { env: PLUGINS_ENABLED, entitlements: ['plugins'] },
  // MCP is a sub-surface of plugins server-side (the mcp router is mounted under
  // the plugins router and requires both entitlements), so the UI gate mirrors
  // that: both build flags and both entitlements must be granted.
  mcp: { env: PLUGINS_ENABLED && MCP_ENABLED, entitlements: ['plugins', 'mcp_servers'] },
  experiences: { env: EXPERIENCES_ENABLED, entitlements: ['experiences'] },
  providers: { env: true, entitlements: ['provider_management'] },
  modelConfigs: { env: true, entitlements: ['model_config_management'] },
};

/**
 * useFeatureEnabled(feature) — true only when the build-time flag AND every
 * required entitlement allow the feature. Entitlements are unknown on
 * self-hosted (and before the first billing poll) — null is treated as "all
 * granted" so those deployments see every feature. Unknown feature keys are
 * treated as ungated (true) — a dev-time wiring mistake shouldn't hide UI.
 */
export const useFeatureEnabled = (feature) => {
  const { entitlements } = useBillingStatus();
  const gate = FEATURE_GATES[feature];
  if (!gate) {
    return true;
  }
  const granted = (key) => entitlements === null || entitlements[key] === true;
  return gate.env && gate.entitlements.every(granted);
};
