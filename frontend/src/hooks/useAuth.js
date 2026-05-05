import { log } from '../utils/log';

import React, { createContext, useContext, useState, useEffect } from 'react';
import { useQueryClient } from 'react-query';
import api, { extractDataFromResponse } from '../services/api';

const AuthContext = createContext();

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState(localStorage.getItem('shu_token'));
  const queryClient = useQueryClient();

  const normalizeUser = (raw) => {
    if (!raw || typeof raw !== 'object') {
      return raw;
    }
    const pick = raw.picture_url || raw.picture || raw.avatar_url || null;
    const avatar = typeof pick === 'string' ? pick.trim() : pick;
    return { ...raw, picture_url: avatar };
  };

  // Initialize authentication state on app load
  useEffect(() => {
    const initAuth = async () => {
      const storedToken = localStorage.getItem('shu_token');
      if (storedToken) {
        try {
          // Verify token and get user info
          const response = await api.get('/auth/me', {
            headers: { Authorization: `Bearer ${storedToken}` },
          });
          const userData = extractDataFromResponse(response);
          setUser(normalizeUser(userData));
          setToken(storedToken);
        } catch (error) {
          log.warn('Token validation failed during initialization:', error.response?.status);
          // Token is invalid, clear it
          localStorage.removeItem('shu_token');
          localStorage.removeItem('shu_refresh_token');
          setToken(null);
          setUser(null);

          // Don't redirect away from public unauthenticated recovery
          // routes — /verify-email and /reset-password identify the
          // visitor by the token in their query string, so a stale
          // session JWT being invalid is irrelevant. The matching
          // routes in App.js render these pages without auth.
          const path = window.location.pathname;
          const isPublicAuthPath =
            path.includes('/auth') || path.startsWith('/verify-email') || path.startsWith('/reset-password');
          if (!isPublicAuthPath) {
            log.info('Redirecting to auth due to invalid token');
            window.location.href = '/auth';
          }
        }
      }
      setLoading(false);
    };

    initAuth();
  }, []);

  const login = async (googleToken) => {
    try {
      const response = await api.post('/auth/login', {
        google_token: googleToken,
      });

      if (response.status === 201) {
        return {
          status: 'pending_activation',
          message: response.data?.detail,
        };
      }

      if (response.status === 200) {
        const responseData = extractDataFromResponse(response);
        const { access_token, refresh_token, user: userData } = responseData;

        // Store tokens
        localStorage.setItem('shu_token', access_token);
        localStorage.setItem('shu_refresh_token', refresh_token);

        // Update state
        setToken(access_token);
        setUser(normalizeUser(userData));

        // Clear any cached queries
        queryClient.clear();

        return {
          status: 'authenticated',
          user: userData,
        };
      }

      log.warn('Unexpected status from Google login attempt', response.status);
      return {
        status: 'unknown',
        data: response.data,
      };
    } catch (error) {
      const message = error.response?.data?.detail || error.response?.data?.error?.message || 'Login failed';
      throw new Error(message);
    }
  };

  const loginWithPassword = async (email, password) => {
    try {
      const response = await api.post('/auth/login/password', {
        email,
        password,
      });

      const responseData = extractDataFromResponse(response);
      const { access_token, refresh_token, user: userData } = responseData;

      // Store tokens
      localStorage.setItem('shu_token', access_token);
      localStorage.setItem('shu_refresh_token', refresh_token);

      // Update state
      setToken(access_token);
      setUser(normalizeUser(userData));

      // Clear any cached queries
      queryClient.clear();

      return userData;
    } catch (error) {
      throw new Error(error.response?.data?.error?.message || 'Login failed');
    }
  };

  const register = async (email, password, name, role = 'regular_user') => {
    try {
      const response = await api.post('/auth/register', {
        email,
        password,
        name,
        role,
      });

      // Backend returns a success envelope with a `status` indicating which
      // gate the new account must clear (SHU-507):
      //   - "activated"                  → admin / first-user, can log in immediately
      //   - "pending_email_verification" → click the link in the verification email
      //   - "pending_admin_activation"   → email backend disabled, admin must activate
      const responseData = extractDataFromResponse(response);
      const knownStatuses = new Set(['activated', 'pending_email_verification', 'pending_admin_activation']);
      if (!knownStatuses.has(responseData?.status)) {
        log.warn('Unexpected register response payload', responseData);
      }

      // Do not set tokens or user; the user must log in after clearing whichever
      // gate the response indicates.
      return responseData;
    } catch (error) {
      throw new Error(error.response?.data?.error?.message || 'Registration failed');
    }
  };

  const verifyEmail = async (token) => {
    try {
      const response = await api.post('/auth/verify-email', { token });
      return extractDataFromResponse(response);
    } catch (error) {
      // Backend wraps every HTTPException into the standard envelope at
      // main.py:http_exception_handler:
      //   { "error": { "code": <HTTP_xxx | structured>, "message": <string> } }
      // The verify-email endpoint surfaces a structured `code` on the
      // expired branch (VERIFICATION_TOKEN_EXPIRED) so the page can switch
      // to the token-based resend UX (no email entry — the token IS the
      // identity, we hand it back to the server). Generic HTTP_xxx codes
      // are filtered out so the page falls through to the unknown-token UI.
      const envelope = error.response?.data?.error;
      const message = envelope?.message || 'Email verification failed';
      const code = envelope?.code && !envelope.code.startsWith('HTTP_') ? envelope.code : null;
      const err = new Error(typeof message === 'string' ? message : 'Email verification failed');
      err.code = code;
      throw err;
    }
  };

  const resendVerification = async (email) => {
    // Always resolves with the generic success envelope — backend does not
    // distinguish unknown / verified / rate-limited cases (no enumeration).
    try {
      const response = await api.post('/auth/resend-verification', { email });
      return extractDataFromResponse(response);
    } catch (error) {
      throw new Error(error.response?.data?.error?.message || 'Resend verification failed');
    }
  };

  const resendVerificationFromToken = async (token) => {
    // Token-based resend: caller passes the original (possibly expired)
    // token and the server resolves the user from its hash. No email
    // address required from the user. Used by the verify-email expired
    // branch — see SHU-507.
    try {
      const response = await api.post('/auth/resend-verification-from-token', { token });
      return extractDataFromResponse(response);
    } catch (error) {
      throw new Error(error.response?.data?.error?.message || 'Resend verification failed');
    }
  };

  const requestPasswordReset = async (email) => {
    // Always resolves with a generic envelope — backend does not
    // distinguish unknown / SSO-only / inactive / rate-limited (no
    // enumeration). SHU-745.
    try {
      const response = await api.post('/auth/request-password-reset', { email });
      return extractDataFromResponse(response);
    } catch (error) {
      throw new Error(error.response?.data?.error?.message || 'Could not request a password reset.');
    }
  };

  const resetPassword = async (token, newPassword) => {
    // SHU-745. Mirrors verifyEmail's structured-error contract: on the
    // expired branch the backend returns code=PASSWORD_RESET_TOKEN_EXPIRED
    // so the page can render a one-click "send a new reset link" CTA
    // (no retype). Auto-generated HTTP_xxx codes are filtered out.
    try {
      const response = await api.post('/auth/reset-password', { token, new_password: newPassword });
      return extractDataFromResponse(response);
    } catch (error) {
      const envelope = error.response?.data?.error;
      const message = envelope?.message || 'Password reset failed';
      const code = envelope?.code && !envelope.code.startsWith('HTTP_') ? envelope.code : null;
      const err = new Error(typeof message === 'string' ? message : 'Password reset failed');
      err.code = code;
      throw err;
    }
  };

  const resendPasswordResetFromToken = async (token) => {
    // SHU-745. Token-as-identity recovery for an expired reset link —
    // hands the original (stale) token back, server resolves the user
    // from its hash and issues a fresh token. No email retype.
    try {
      const response = await api.post('/auth/resend-password-reset-from-token', { token });
      return extractDataFromResponse(response);
    } catch (error) {
      throw new Error(error.response?.data?.error?.message || 'Could not resend reset email.');
    }
  };

  const logout = () => {
    // Clear tokens
    localStorage.removeItem('shu_token');
    localStorage.removeItem('shu_refresh_token');

    // Clear state
    setToken(null);
    setUser(null);

    // Clear cached queries
    queryClient.clear();
  };

  const refreshToken = async () => {
    const storedRefreshToken = localStorage.getItem('shu_refresh_token');
    if (!storedRefreshToken) {
      throw new Error('No refresh token available');
    }

    try {
      const response = await api.post('/auth/refresh', {
        refresh_token: storedRefreshToken,
      });

      const responseData = extractDataFromResponse(response);
      const { access_token, refresh_token: newRefreshToken, user: userData } = responseData;

      // Store new tokens
      localStorage.setItem('shu_token', access_token);
      localStorage.setItem('shu_refresh_token', newRefreshToken);

      // Update state
      setToken(access_token);
      setUser(normalizeUser(userData));

      return { access_token, refresh_token: newRefreshToken, user: userData };
    } catch (error) {
      // Refresh failed, clear tokens
      localStorage.removeItem('shu_token');
      localStorage.removeItem('shu_refresh_token');
      setToken(null);
      setUser(null);

      throw new Error(error.response?.data?.error?.message || 'Token refresh failed');
    }
  };

  const refreshUser = async () => {
    try {
      const response = await api.get('/auth/me');
      const userData = extractDataFromResponse(response);
      setUser(normalizeUser(userData));
      return userData;
    } catch (error) {
      log.warn('Failed to refresh user:', error.response?.status);
      throw error;
    }
  };

  const handleAuthError = () => {
    log.warn('Authentication error detected - logging out user');
    logout();

    // Same exemption as initAuth: public unauthenticated recovery routes
    // identify the visitor by the URL token, not by session, so they
    // should not be redirected to /auth even when an invalid JWT was
    // detected on a parallel API call.
    const path = window.location.pathname;
    const isPublicAuthPath =
      path.includes('/auth') || path.startsWith('/verify-email') || path.startsWith('/reset-password');
    if (!isPublicAuthPath) {
      window.location.href = '/auth';
    }
  };

  const hasRole = (requiredRole) => {
    if (!user) {
      return false;
    }

    const roleHierarchy = {
      regular_user: 1,
      power_user: 2,
      admin: 3,
    };

    const userLevel = roleHierarchy[user.role] || 0;
    const requiredLevel = roleHierarchy[requiredRole] || 0;

    return userLevel >= requiredLevel;
  };

  const canAccessAdminPanel = () => {
    return hasRole('power_user');
  };

  const canManagePromptsAndModels = () => {
    return hasRole('power_user');
  };

  const canManageUsers = () => {
    return hasRole('admin');
  };

  const value = {
    user,
    token,
    loading,
    login,
    loginWithPassword,
    register,
    verifyEmail,
    resendVerification,
    resendVerificationFromToken,
    requestPasswordReset,
    resetPassword,
    resendPasswordResetFromToken,
    refreshToken,
    refreshUser,
    logout,
    handleAuthError,
    hasRole,
    canAccessAdminPanel,
    canManagePromptsAndModels,
    canManageUsers,
    isAuthenticated: !!user,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
