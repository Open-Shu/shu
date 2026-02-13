import React, { createContext, useContext, useEffect, useState, useMemo, useCallback } from 'react';
import { createTheme } from '@mui/material/styles';
import { useAuth } from '../hooks/useAuth';
import { userPreferencesAPI, brandingAPI, extractDataFromResponse } from '../services/api';
import {
  defaultBranding,
  resolveBranding,
  getThemeConfig,
  getPrimaryColor,
  getBrandingFaviconUrlForTheme,
  getBrandingAppName,
} from '../utils/constants';
import log from '../utils/log';

const ThemeContext = createContext();

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};

export const ThemeProvider = ({ children }) => {
  const { user, isAuthenticated } = useAuth();
  const [themeMode, setThemeMode] = useState('light'); // 'light', 'dark', 'auto'
  const [resolvedMode, setResolvedMode] = useState('light'); // actual mode after resolving 'auto'
  const [branding, setBrandingState] = useState(() => resolveBranding(defaultBranding));
  const [brandingLoaded, setBrandingLoaded] = useState(false);

  const setBranding = useCallback(
    (nextBranding) => {
      setBrandingState(resolveBranding(nextBranding));
      setBrandingLoaded(true);
    },
    [setBrandingLoaded]
  );

  const refreshBranding = useCallback(async () => {
    setBrandingLoaded(false);
    try {
      const response = await brandingAPI.getBranding();
      const data = extractDataFromResponse(response);
      setBranding(data);
    } catch (error) {
      log.warn('Failed to load branding, using defaults:', error);
      setBranding(defaultBranding);
    }
  }, [setBranding, setBrandingLoaded]);

  useEffect(() => {
    refreshBranding();
  }, [refreshBranding]);

  useEffect(() => {
    if (isAuthenticated) {
      userPreferencesAPI
        .getPreferences()
        .then((response) => {
          const prefs = extractDataFromResponse(response);
          setThemeMode(prefs.theme || 'light');
        })
        .catch((err) => {
          log.warn('Failed to load theme preference, using default:', err);
          setThemeMode('light');
        });
    } else {
      // Use localStorage for non-authenticated users
      const savedTheme = localStorage.getItem('shu-theme') || 'light';
      setThemeMode(savedTheme);
    }
  }, [isAuthenticated, user]);

  // Resolve 'auto' mode based on system preference
  useEffect(() => {
    if (themeMode === 'auto') {
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      setResolvedMode(mediaQuery.matches ? 'dark' : 'light');

      const handleChange = (e) => {
        setResolvedMode(e.matches ? 'dark' : 'light');
      };

      mediaQuery.addEventListener('change', handleChange);
      return () => mediaQuery.removeEventListener('change', handleChange);
    } else {
      setResolvedMode(themeMode);
    }
  }, [themeMode]);

  // Create MUI theme based on resolved mode
  const theme = useMemo(() => {
    return createTheme(getThemeConfig(resolvedMode, branding));
  }, [resolvedMode, branding]);

  // Insert branding information into DOM dynamicalls
  useEffect(() => {
    if (typeof document === 'undefined') {
      return;
    }

    const resolved = resolveBranding(branding);
    const faviconUrl = getBrandingFaviconUrlForTheme(resolved, resolvedMode);
    const appName = getBrandingAppName(resolved);

    const upsertLink = (selector, rel, href) => {
      if (!href) {
        return;
      }

      let link = document.querySelector(selector);
      if (!link) {
        link = document.createElement('link');
        link.setAttribute('rel', rel);
        document.head.appendChild(link);
      }

      link.setAttribute('href', href);
    };

    upsertLink('link[rel="icon"]', 'icon', faviconUrl);
    upsertLink('link[rel="shortcut icon"]', 'shortcut icon', faviconUrl);
    upsertLink('link[rel="apple-touch-icon"]', 'apple-touch-icon', faviconUrl);

    const themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeMeta) {
      themeMeta.setAttribute('content', getPrimaryColor(resolvedMode, resolved));
    }

    if (appName) {
      document.title = appName;
    }
  }, [branding, resolvedMode]);

  const changeTheme = useCallback(
    async (newTheme) => {
      setThemeMode(newTheme);

      // Save to localStorage for non-authenticated users
      localStorage.setItem('shu-theme', newTheme);

      // Save to backend if authenticated
      if (isAuthenticated) {
        try {
          await userPreferencesAPI.patchPreferences({ theme: newTheme });
          log.info('Theme preference saved:', newTheme);
        } catch (error) {
          log.warn('Failed to save theme preference:', error);
        }
      }
    },
    [isAuthenticated]
  );

  const value = {
    theme,
    themeMode,
    resolvedMode,
    changeTheme,
    isDark: resolvedMode === 'dark',
    branding,
    brandingLoaded,
    refreshBranding,
    setBranding,
  };

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};
