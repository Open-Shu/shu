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
import {
  resolveFontFamily,
  resolveHeadingFontFamily,
  resolveFontSizeScale,
  computeRootFontSizePercent,
} from '../utils/typography';
import log from '../utils/log';

const ThemeContext = createContext();

const LS_THEME_KEY = 'shu-theme';
const LS_FONT_FAMILY_KEY = 'shu-font-family';
const LS_FONT_SCALE_KEY = 'shu-font-scale';

const readLocal = (key) => {
  if (typeof localStorage === 'undefined') {
    return null;
  }
  try {
    return localStorage.getItem(key);
  } catch (_) {
    return null;
  }
};

const writeLocal = (key, value) => {
  if (typeof localStorage === 'undefined') {
    return;
  }
  try {
    if (value === null || value === undefined) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, value);
    }
  } catch (_) {
    /* localStorage write failures are non-fatal */
  }
};

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};

export const ThemeProvider = ({ children }) => {
  const { user, isAuthenticated } = useAuth();
  const [themeMode, setThemeMode] = useState('auto'); // 'light', 'dark', 'auto'
  const [resolvedMode, setResolvedMode] = useState(() =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  ); // actual mode after resolving 'auto'
  const [branding, setBrandingState] = useState(() => resolveBranding(defaultBranding));
  const [brandingLoaded, setBrandingLoaded] = useState(false);

  // Typography prefs: null = inherit (cascade resolves to brand → shipped default).
  // Seed from localStorage so anonymous users see their last-picked font without
  // waiting for the server preference round-trip.
  const [userFontFamily, setUserFontFamily] = useState(() => readLocal(LS_FONT_FAMILY_KEY));
  const [userFontScale, setUserFontScale] = useState(() => readLocal(LS_FONT_SCALE_KEY));

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
          setThemeMode(prefs.theme || 'auto');
          // Server pref overrides local fallback (handles "logged in as a
          // different user on a shared device" cleanly).
          setUserFontFamily(prefs.font_family ?? null);
          setUserFontScale(prefs.font_size_scale ?? null);
          writeLocal(LS_FONT_FAMILY_KEY, prefs.font_family ?? null);
          writeLocal(LS_FONT_SCALE_KEY, prefs.font_size_scale ?? null);
        })
        .catch((err) => {
          log.warn('Failed to load user preferences, using defaults:', err);
          setThemeMode('auto');
        });
    } else {
      // Use localStorage for non-authenticated users (theme + fonts).
      const savedTheme = readLocal(LS_THEME_KEY) || 'auto';
      setThemeMode(savedTheme);
      setUserFontFamily(readLocal(LS_FONT_FAMILY_KEY));
      setUserFontScale(readLocal(LS_FONT_SCALE_KEY));
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

  // Resolve typography cascade.
  const resolvedFontFamily = useMemo(
    () => resolveFontFamily(userFontFamily, branding.brandFontFamily),
    [userFontFamily, branding.brandFontFamily]
  );
  const resolvedHeadingFontFamily = useMemo(
    () => resolveHeadingFontFamily(branding.brandHeadingFontFamily, userFontFamily, branding.brandFontFamily),
    [branding.brandHeadingFontFamily, userFontFamily, branding.brandFontFamily]
  );
  const resolvedFontScale = useMemo(() => resolveFontSizeScale(userFontScale), [userFontScale]);

  // Apply root font-size as a percentage so we scale relative to the user's
  // browser-level font-size accessibility setting (px would override it).
  useEffect(() => {
    if (typeof document === 'undefined') {
      return;
    }
    document.documentElement.style.fontSize = computeRootFontSizePercent(resolvedFontScale);
  }, [resolvedFontScale]);

  // Create MUI theme based on resolved mode + resolved body/heading fonts.
  // The font size scale is *not* a theme dep — it lives on the HTML root
  // via the effect above, and rem-based sizes pick it up at render time.
  const theme = useMemo(() => {
    return createTheme(getThemeConfig(resolvedMode, branding, userFontFamily));
  }, [resolvedMode, branding, userFontFamily]);

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
      writeLocal(LS_THEME_KEY, newTheme);

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

  const changeFontFamily = useCallback(
    async (newFontFamily) => {
      // Treat empty string / undefined as null (= inherit).
      const normalized = newFontFamily || null;
      setUserFontFamily(normalized);
      writeLocal(LS_FONT_FAMILY_KEY, normalized);
      if (isAuthenticated) {
        try {
          await userPreferencesAPI.patchPreferences({ font_family: normalized });
        } catch (error) {
          log.warn('Failed to save font_family preference:', error);
        }
      }
    },
    [isAuthenticated]
  );

  const changeFontScale = useCallback(
    async (newScale) => {
      const normalized = newScale || null;
      setUserFontScale(normalized);
      writeLocal(LS_FONT_SCALE_KEY, normalized);
      if (isAuthenticated) {
        try {
          await userPreferencesAPI.patchPreferences({ font_size_scale: normalized });
        } catch (error) {
          log.warn('Failed to save font_size_scale preference:', error);
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
    // Typography
    userFontFamily,
    userFontScale,
    resolvedFontFamily,
    resolvedHeadingFontFamily,
    resolvedFontScale,
    changeFontFamily,
    changeFontScale,
  };

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};
