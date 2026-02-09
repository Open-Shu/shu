import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Button, Box, Typography, Alert, CircularProgress, Paper, Container } from '@mui/material';
import GoogleIcon from '@mui/icons-material/Google';
import { useAuth } from '../hooks/useAuth';
import { authAPI, extractDataFromResponse } from '../services/api';
import configService from '../services/config';
import { log } from '../utils/log';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import { getBrandingAppName } from '../utils/constants';
import { getApiV1Base } from '../services/baseUrl';

const GoogleLogin = ({ onSwitchToPassword }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);
  const [googleLoaded, setGoogleLoaded] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [showRedirect, setShowRedirect] = useState(false);

  const { login } = useAuth();
  const { branding } = useAppTheme();
  const appDisplayName = getBrandingAppName(branding);
  const logoUrl = branding?.logoUrl;

  useEffect(() => {
    const initializeComponent = async () => {
      try {
        // Load configuration from backend
        await configService.fetchConfig();
        setConfigLoaded(true);

        // Check if Google library is already loaded
        if (window.google) {
          setGoogleLoaded(true);
          return;
        }

        // Wait for Google library to load
        const checkGoogleLoaded = () => {
          if (window.google) {
            setGoogleLoaded(true);
          } else {
            setTimeout(checkGoogleLoaded, 100);
          }
        };

        checkGoogleLoaded();
      } catch (err) {
        setError(`Failed to load configuration: ${err.message}`);
      }
    };

    initializeComponent();
  }, []);

  const gsiBtnRef = useRef(null);
  // Refs for redirect popup and listener management
  const messageHandlerRef = useRef(null);
  const timeoutRef = useRef(null);
  const popupRef = useRef(null);

  // Stable handler for GIS callback
  const handleCredentialResponse = useCallback(
    async (response) => {
      try {
        // Send the Google token to our backend
        const result = await login(response.credential);

        if (result?.status === 'pending_activation') {
          setSuccessMessage(
            result.message ||
              'Your account has been created but requires administrator activation before you can sign in.'
          );
          setLoading(false);
          return;
        }

        if (result?.status && result.status !== 'authenticated') {
          setError('Unexpected response from authentication service. Please try again.');
          setLoading(false);
          return;
        }

        // Redirect to main app (the auth wrapper will handle the redirect)
        window.location.href = '/';
      } catch (err) {
        setError(err.message || 'Login failed');
        setLoading(false);
      }
    },
    [login]
  );

  // Initialize GIS with FedCM and render the official button when ready
  useEffect(() => {
    const initGIS = async () => {
      try {
        if (!configLoaded || !googleLoaded || !window.google) {
          return;
        }

        const googleClientId = configService.getGoogleClientId();
        if (!googleClientId) {
          setError('Google Client ID not configured. Please contact your administrator.');
          return;
        }

        // Initialize GIS with FedCM flags
        window.google.accounts.id.initialize({
          client_id: googleClientId,
          callback: handleCredentialResponse,
          use_fedcm_for_prompt: true,
          use_fedcm_for_button: true,
        });

        // Render the official Google button into the container
        if (gsiBtnRef.current) {
          window.google.accounts.id.renderButton(gsiBtnRef.current, {
            theme: 'outline',
            size: 'large',
            text: 'signin_with',
            locale: navigator.language || 'en',
          });
        }

        // Do not auto-prompt. We only show account chooser after the user clicks the button.
      } catch (e) {
        log.error('Failed to initialize Google Identity Services', e);
      }
    };

    initGIS();
  }, [configLoaded, googleLoaded, handleCredentialResponse]);
  // Cleanup message listener, timeout, and popup on unmount
  useEffect(() => {
    return () => {
      try {
        if (messageHandlerRef.current) {
          window.removeEventListener('message', messageHandlerRef.current);
          messageHandlerRef.current = null;
        }
        if (timeoutRef.current) {
          clearTimeout(timeoutRef.current);
          timeoutRef.current = null;
        }
        try {
          popupRef.current && popupRef.current.close();
        } catch (_) {
          // Ignore error
        }
      } catch (_) {
        // Ignore error
      }
    };
  }, []);

  const handleRedirectLogin = async () => {
    setLoading(true);
    setError(null);
    setSuccessMessage(null);

    try {
      const url = `${getApiV1Base()}/auth/google/login`;
      const popup = window.open(
        url,
        'shu-google-oauth',
        'width=500,height=650,menubar=no,toolbar=no,location=no,status=no'
      );

      // Track popup for cleanup

      popupRef.current = popup || null;

      if (!popup) {
        // Popup blocked - fallback to top-level redirect
        window.location.href = url;
        return;
      }

      const expectedOrigin = new URL(getApiV1Base()).origin;

      const onMessage = async (event) => {
        try {
          if (event.origin !== expectedOrigin) {
            return;
          }
          const data = event.data || {};
          if (!data || !data.code || data.provider !== 'google') {
            return;
          }

          // Cleanup listener/timeout and close popup
          if (messageHandlerRef.current) {
            window.removeEventListener('message', messageHandlerRef.current);
            messageHandlerRef.current = null;
          }
          if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
            timeoutRef.current = null;
          }
          try {
            popupRef.current && popupRef.current.close();
          } catch (_) {
            // Ignore error
          }

          const resp = await authAPI.exchangeGoogleLogin(data.code);
          const payload = extractDataFromResponse(resp);
          if (!payload || !payload.access_token) {
            throw new Error('Unexpected response from authentication service');
          }

          // Store tokens and redirect; AuthProvider will initialize user
          localStorage.setItem('shu_token', payload.access_token);
          if (payload.refresh_token) {
            localStorage.setItem('shu_refresh_token', payload.refresh_token);
          }

          window.location.href = '/';
        } catch (ex) {
          setError(ex.message || 'Login exchange failed');
          setLoading(false);
        }
      };

      // Store handler and add listener
      messageHandlerRef.current = onMessage;
      window.addEventListener('message', onMessage);

      // Timeout to auto-clean if no message arrives
      timeoutRef.current = setTimeout(() => {
        try {
          if (messageHandlerRef.current) {
            window.removeEventListener('message', messageHandlerRef.current);
            messageHandlerRef.current = null;
          }
          try {
            popupRef.current && popupRef.current.close();
          } catch (_) {
            // Ignore error
          }
        } finally {
          setLoading(false);
          setError('Login window timed out. Please try again or use the primary Google button.');
        }
      }, 180000);
    } catch (err) {
      setError(err.message || 'Failed to start redirect login');
      setLoading(false);
    }
  };

  return (
    <Container maxWidth="sm">
      <Box
        sx={{
          marginTop: 8,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
        }}
      >
        <Paper elevation={3} sx={{ padding: 4, width: '100%' }}>
          <Box sx={{ textAlign: 'center', mb: 3 }}>
            <img
              src={logoUrl}
              alt={appDisplayName}
              style={{
                height: '60px', // Fixed height for normal proportions
                width: 'auto', // Maintain aspect ratio
                maxWidth: '100%', // Don't exceed container width
                marginBottom: '1rem',
              }}
            />
            <Typography component="h1" variant="h4" gutterBottom>
              Sign in to {appDisplayName}
            </Typography>
            <Typography variant="body1" color="text.secondary">
              Sign in with your Google account to continue
            </Typography>
          </Box>

          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          {successMessage && (
            <Alert severity="success" sx={{ mb: 2 }}>
              {successMessage}
            </Alert>
          )}

          {(!configLoaded || !googleLoaded) && (
            <Alert severity="info" sx={{ mb: 2 }}>
              {!configLoaded ? 'Loading configuration...' : 'Loading Google OAuth library...'}
            </Alert>
          )}

          {/* Primary Google button (GIS FedCM). Renders when library is available */}
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 2 }}>
            <div ref={gsiBtnRef} />
          </Box>

          {/* Help + optional redirect fallback (hidden by default) */}
          <Box sx={{ textAlign: 'center', mt: 1 }}>
            <Typography variant="body2" color="text.secondary">
              Having trouble signing in?
              <Button
                variant="text"
                size="small"
                sx={{ ml: 1 }}
                onClick={() => setShowRedirect((v) => !v)}
                disabled={!configLoaded}
              >
                {showRedirect ? 'Hide redirect' : 'Try redirect'}
              </Button>
            </Typography>

            {showRedirect && (
              <Box sx={{ mt: 1 }}>
                <Button
                  variant="outlined"
                  size="medium"
                  onClick={handleRedirectLogin}
                  disabled={loading || !configLoaded}
                  startIcon={loading ? <CircularProgress size={16} /> : <GoogleIcon fontSize="small" />}
                >
                  {loading ? 'Starting...' : 'Sign in with Google (redirect)'}
                </Button>
              </Box>
            )}
          </Box>

          {onSwitchToPassword && (
            <Box sx={{ textAlign: 'center', mt: 2 }}>
              <Button variant="text" onClick={onSwitchToPassword} disabled={loading}>
                Sign in with email and password instead
              </Button>
            </Box>
          )}

          <Typography variant="caption" display="block" sx={{ textAlign: 'center', mt: 2 }}>
            Only authorized {appDisplayName} accounts can access this system
          </Typography>
        </Paper>
      </Box>
    </Container>
  );
};

export default GoogleLogin;
