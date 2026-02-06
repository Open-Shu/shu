import { useCallback } from 'react';
import { useQueryClient } from 'react-query';
import { hostAuthAPI, extractDataFromResponse, formatError } from '../services/api';
import { getApiBaseUrl } from '../services/baseUrl';

/**
 * useOAuthAuthorize
 * Centralizes the OAuth popup + exchange + cache invalidation flow.
 *
 * API:
 *   const { startAuthorize } = useOAuthAuthorize();
 *   startAuthorize({
 *     provider: 'google',
 *     scopes: ['scope1','scope2'], // optional; if omitted, server computes union
 *     onStart: () => setAuthorizing(true),
 *     onDone: () => setAuthorizing(false),
 *     onError: (e) => console.error(e),
 *     onAfterExchange: async () => { optional post-exchange work },
 *   });
 */
export default function useOAuthAuthorize() {
  const qc = useQueryClient();

  const startAuthorize = useCallback(
    async ({ provider, scopes, onStart, onDone, onError, onAfterExchange }) => {
      const prov = String(provider || '').toLowerCase();
      const desiredScopes = Array.isArray(scopes) ? scopes.filter(Boolean).map(String) : [];
      try {
        onStart && onStart();
        const scopesCsv = desiredScopes.length ? desiredScopes.join(',') : undefined;
        const resp = await hostAuthAPI.authorize(prov, scopesCsv);
        const data = extractDataFromResponse(resp);
        const url = data.authorization_url;
        const popup = window.open(url, 'oauth_popup', 'width=500,height=700');

        const listener = async (event) => {
          try {
            const expectedFrontend = window.location.origin;
            let expectedApi = expectedFrontend;
            try {
              expectedApi = new URL(getApiBaseUrl()).origin;
            } catch (_) {
              // Ignore error
            }
            if (event.origin !== expectedFrontend && event.origin !== expectedApi) {
              return;
            }
            if (!event.data || typeof event.data !== 'object') {
              return;
            }
            const { provider: evProvider, code } = event.data || {};
            if (String(evProvider || '').toLowerCase() !== prov || !code) {
              return;
            }

            window.removeEventListener('message', listener);
            if (popup && !popup.closed) {
              popup.close();
            }

            // Exchange code for tokens with requested scopes (if provided)
            await hostAuthAPI.exchange({
              provider: prov,
              code,
              scopes: desiredScopes.length ? desiredScopes : undefined,
            });

            // Default cache invalidation
            try {
              qc.invalidateQueries(['hostAuth', 'status']);
              qc.invalidateQueries(['hostAuth', 'consentScopes', prov]);
            } catch (_) {
              // Ignore error
            }

            if (typeof onAfterExchange === 'function') {
              await onAfterExchange();
            }
          } catch (e) {
            onError && onError(e);
            // eslint-disable-next-line no-console
            console.error('OAuth exchange failed:', formatError(e));
          } finally {
            onDone && onDone();
          }
        };

        window.addEventListener('message', listener);
        // Fallback re-enable in case popup is closed without message
        const timer = setInterval(() => {
          if (popup && popup.closed) {
            try {
              window.removeEventListener('message', listener);
            } catch (_) {
              // Ignore error
            }
            onDone && onDone();
            clearInterval(timer);
          }
        }, 1000);
      } catch (e) {
        onError && onError(e);
        // eslint-disable-next-line no-console
        console.error('Authorize failed:', formatError(e));
        onDone && onDone();
      }
    },
    [qc]
  );

  return { startAuthorize };
}
