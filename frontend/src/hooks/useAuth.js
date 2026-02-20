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

          // If we're not already on the auth page, redirect there
          if (!window.location.pathname.includes('/auth')) {
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

      // Backend returns a success message and pending activation status; no tokens
      const responseData = extractDataFromResponse(response);
      if (responseData?.status !== 'pending_activation') {
        log.warn('Unexpected register response payload', responseData);
      }

      // Do not set tokens or user; require login after activation by admin
      return responseData;
    } catch (error) {
      throw new Error(error.response?.data?.error?.message || 'Registration failed');
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

    // Redirect to auth if not already there
    if (!window.location.pathname.includes('/auth')) {
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
