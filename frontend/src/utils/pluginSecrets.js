/**
 * Utility functions for plugin secrets handling.
 */

/**
 * Check if a secret scope allows user configuration.
 *
 * @param {string|undefined} allowedScope - The allowed_scope from op_auth.secrets spec
 * @returns {boolean} True if users can configure this secret (user or system_or_user scope)
 */
export function isUserConfigurableScope(allowedScope) {
  const scope = allowedScope || 'system_or_user';
  return scope === 'user' || scope === 'system_or_user';
}

/**
 * Extract user-configurable secret keys from a plugin's op_auth configuration.
 *
 * @param {Object} opAuth - The plugin's op_auth object
 * @returns {Set<string>} Set of secret keys that users can configure
 */
export function extractUserConfigurableSecretKeys(opAuth) {
  const secretKeys = new Set();
  if (!opAuth || typeof opAuth !== 'object') {
    return secretKeys;
  }

  for (const op of Object.keys(opAuth)) {
    const spec = opAuth[op];
    const secrets = spec?.secrets;
    if (secrets && typeof secrets === 'object') {
      for (const key of Object.keys(secrets)) {
        const secretSpec = secrets[key];
        if (isUserConfigurableScope(secretSpec?.allowed_scope)) {
          if (key) {
            secretKeys.add(key);
          }
        }
      }
    }
  }

  return secretKeys;
}
