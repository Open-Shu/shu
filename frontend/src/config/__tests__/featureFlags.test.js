import { describe, test, expect, vi, beforeEach } from 'vitest';

// Helper: dynamically import the module after stubbing env vars
const loadFlags = async () => {
  vi.resetModules();
  const mod = await import('../featureFlags.js');
  return mod;
};

describe('featureFlags', () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  test('all flags default to true when env vars are empty', async () => {
    // Simulate unset by using empty strings (Vite .env may inject values;
    // empty strings trigger the fallback path in parseBoolean)
    vi.stubEnv('VITE_PLUGINS_ENABLED', '');
    vi.stubEnv('VITE_MCP_ENABLED', '');
    vi.stubEnv('VITE_EXPERIENCES_ENABLED', '');
    const { PLUGINS_ENABLED, MCP_ENABLED, EXPERIENCES_ENABLED } = await loadFlags();
    expect(PLUGINS_ENABLED).toBe(true);
    expect(MCP_ENABLED).toBe(true);
    expect(EXPERIENCES_ENABLED).toBe(true);
  });

  test('flags are false when env vars are set to "false"', async () => {
    vi.stubEnv('VITE_PLUGINS_ENABLED', 'false');
    vi.stubEnv('VITE_MCP_ENABLED', 'false');
    vi.stubEnv('VITE_EXPERIENCES_ENABLED', 'false');
    const { PLUGINS_ENABLED, MCP_ENABLED, EXPERIENCES_ENABLED } = await loadFlags();
    expect(PLUGINS_ENABLED).toBe(false);
    expect(MCP_ENABLED).toBe(false);
    expect(EXPERIENCES_ENABLED).toBe(false);
  });

  test('flags are true when env vars are set to "true"', async () => {
    vi.stubEnv('VITE_PLUGINS_ENABLED', 'true');
    vi.stubEnv('VITE_MCP_ENABLED', 'true');
    vi.stubEnv('VITE_EXPERIENCES_ENABLED', 'true');
    const { PLUGINS_ENABLED, MCP_ENABLED, EXPERIENCES_ENABLED } = await loadFlags();
    expect(PLUGINS_ENABLED).toBe(true);
    expect(MCP_ENABLED).toBe(true);
    expect(EXPERIENCES_ENABLED).toBe(true);
  });

  test('flags default to true for unrecognized values', async () => {
    vi.stubEnv('VITE_PLUGINS_ENABLED', 'yes');
    vi.stubEnv('VITE_MCP_ENABLED', '1');
    vi.stubEnv('VITE_EXPERIENCES_ENABLED', 'maybe');
    const { PLUGINS_ENABLED, MCP_ENABLED, EXPERIENCES_ENABLED } = await loadFlags();
    expect(PLUGINS_ENABLED).toBe(true);
    expect(MCP_ENABLED).toBe(true);
    expect(EXPERIENCES_ENABLED).toBe(true);
  });

  test('flags handle case-insensitive values', async () => {
    vi.stubEnv('VITE_PLUGINS_ENABLED', 'FALSE');
    vi.stubEnv('VITE_MCP_ENABLED', 'True');
    vi.stubEnv('VITE_EXPERIENCES_ENABLED', ' False ');
    const { PLUGINS_ENABLED, MCP_ENABLED, EXPERIENCES_ENABLED } = await loadFlags();
    expect(PLUGINS_ENABLED).toBe(false);
    expect(MCP_ENABLED).toBe(true);
    expect(EXPERIENCES_ENABLED).toBe(false);
  });

  test('flags can be toggled independently', async () => {
    vi.stubEnv('VITE_PLUGINS_ENABLED', 'false');
    vi.stubEnv('VITE_MCP_ENABLED', 'true');
    vi.stubEnv('VITE_EXPERIENCES_ENABLED', '');
    const { PLUGINS_ENABLED, MCP_ENABLED, EXPERIENCES_ENABLED } = await loadFlags();
    expect(PLUGINS_ENABLED).toBe(false);
    expect(MCP_ENABLED).toBe(true);
    expect(EXPERIENCES_ENABLED).toBe(true);
  });
});
