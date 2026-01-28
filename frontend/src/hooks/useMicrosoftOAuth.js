import { useState, useRef, useEffect, useCallback } from 'react';
import { authAPI, extractDataFromResponse } from '../services/api';
import { getApiV1Base } from '../services/baseUrl';
import { log } from '../utils/log';

// OAuth popup timeout in milliseconds (3 minutes)
const OAUTH_POPUP_TIMEOUT_MS = 180000;

/**
 * Custom hook for Microsoft OAuth popup authentication flow.
 * 
 * @param {Object} options
 * @param {Function} options.onSuccess - Called with tokens on successful login
 * @param {Function} options.onError - Called with error message on failure
 * @param {Function} options.onPendingActivation - Called when account needs admin activation
 * @returns {Object} { startLogin, loading }
 */
export function useMicrosoftOAuth({ onSuccess, onError, onPendingActivation }) {
  const [loading, setLoading] = useState(false);
  
  // Refs for popup and listener management
  const messageHandlerRef = useRef(null);
  const timeoutRef = useRef(null);
  const popupRef = useRef(null);

  // Cleanup function
  const cleanup = useCallback(() => {
    if (messageHandlerRef.current) {
      window.removeEventListener('message', messageHandlerRef.current);
      messageHandlerRef.current = null;
    }
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    try {
      if (popupRef.current) {
        popupRef.current.close();
      }
    } catch (_) {
      // Ignore errors closing popup
    }
    popupRef.current = null;
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return cleanup;
  }, [cleanup]);

  const startLogin = useCallback(async () => {
    setLoading(true);
    
    try {
      const url = `${getApiV1Base()}/auth/microsoft/login`;
      const popup = window.open(
        url,
        'shu-microsoft-oauth',
        'width=500,height=650,menubar=no,toolbar=no,location=no,status=no'
      );

      popupRef.current = popup || null;

      if (!popup) {
        // Popup blocked - fallback to top-level redirect
        log.info('Microsoft OAuth popup blocked, falling back to redirect');
        window.location.href = url;
        return;
      }

      const expectedOrigin = new URL(getApiV1Base()).origin;

      const onMessage = async (event) => {
        try {
          if (event.origin !== expectedOrigin) return;
          const data = event.data || {};
          if (!data || !data.code || data.provider !== 'microsoft') return;

          // Cleanup listener/timeout and close popup
          cleanup();

          const resp = await authAPI.exchangeMicrosoftLogin(data.code);
          const payload = extractDataFromResponse(resp);
          
          // Check for pending activation (201 response without tokens)
          if (!payload || !payload.access_token) {
            log.info('Microsoft OAuth: account pending activation');
            if (onPendingActivation) {
              onPendingActivation();
            }
            setLoading(false);
            return;
          }

          // Success - call onSuccess with tokens
          log.info('Microsoft OAuth: login successful');
          if (onSuccess) {
            onSuccess({
              accessToken: payload.access_token,
              refreshToken: payload.refresh_token,
              user: payload.user,
            });
          }
          setLoading(false);
        } catch (ex) {
          // Check if this is a 201 pending activation response
          if (ex.response?.status === 201) {
            log.info('Microsoft OAuth: account pending activation (201 response)');
            if (onPendingActivation) {
              onPendingActivation();
            }
            setLoading(false);
            return;
          }
          
          const errorMessage = ex.response?.data?.detail || ex.message || 'Microsoft login failed';
          log.error('Microsoft OAuth exchange failed', { error: errorMessage });
          if (onError) {
            onError(errorMessage);
          }
          setLoading(false);
        }
      };

      messageHandlerRef.current = onMessage;
      window.addEventListener('message', onMessage);

      // Timeout after configured duration
      timeoutRef.current = setTimeout(() => {
        cleanup();
        setLoading(false);
        log.warn('Microsoft OAuth popup timed out');
        if (onError) {
          onError('Login window timed out. Please try again.');
        }
      }, OAUTH_POPUP_TIMEOUT_MS);
      
    } catch (err) {
      const errorMessage = err.message || 'Failed to start Microsoft login';
      log.error('Microsoft OAuth start failed', { error: errorMessage });
      if (onError) {
        onError(errorMessage);
      }
      setLoading(false);
    }
  }, [cleanup, onSuccess, onError, onPendingActivation]);

  return { startLogin, loading };
}

export default useMicrosoftOAuth;
