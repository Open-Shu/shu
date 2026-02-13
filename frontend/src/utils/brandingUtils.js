/**
 * Branding utility functions for managing theme-aware branding assets and colors.
 *
 * This module provides utilities for:
 * - Resolving branding configuration from API responses
 * - Selecting appropriate assets based on theme mode
 * - Managing theme-aware text colors
 */

const defaultFaviconUrl = '/favicon-dark.png';
const defaultDarkFaviconUrl = '/favicon-dark.png';

/**
 * Helper function to convert values to plain objects.
 * @param {*} value - The value to convert
 * @returns {Object} Plain object or empty object
 */
const toPlainObject = (value) => (value && typeof value === 'object' && !Array.isArray(value) ? value : {});

/**
 * Default branding configuration used when no custom branding is set.
 */
export const defaultBranding = {
  appName: 'Shu',
  faviconUrl: defaultFaviconUrl,
  darkFaviconUrl: defaultDarkFaviconUrl,
  lightTopbarTextColor: null,
  darkTopbarTextColor: null,
  lightThemeOverrides: {},
  darkThemeOverrides: {},
  updatedAt: null,
  updatedBy: null,
};

/**
 * Resolve branding configuration from API response.
 * Handles both camelCase and snake_case field names from the backend.
 *
 * @param {Object} branding - Raw branding object from API
 * @returns {Object} Resolved branding configuration with all fields
 */
/* eslint-disable complexity */
export const resolveBranding = (branding) => {
  const raw = branding || {};
  const resolved = {
    appName: raw.appName ?? raw.app_name ?? defaultBranding.appName,
    faviconUrl: raw.faviconUrl ?? raw.favicon_url ?? defaultFaviconUrl,
    darkFaviconUrl: raw.darkFaviconUrl ?? raw.dark_favicon_url ?? defaultDarkFaviconUrl,
    lightTopbarTextColor: raw.lightTopbarTextColor ?? raw.light_topbar_text_color ?? null,
    darkTopbarTextColor: raw.darkTopbarTextColor ?? raw.dark_topbar_text_color ?? null,
    lightThemeOverrides: toPlainObject(raw.lightThemeOverrides ?? raw.light_theme_overrides) || {},
    darkThemeOverrides: toPlainObject(raw.darkThemeOverrides ?? raw.dark_theme_overrides) || {},
    updatedAt: raw.updatedAt ?? raw.updated_at ?? defaultBranding.updatedAt,
    updatedBy: raw.updatedBy ?? raw.updated_by ?? defaultBranding.updatedBy,
  };

  return {
    ...defaultBranding,
    ...resolved,
  };
};

/**
 * Get the favicon URL from branding configuration.
 *
 * @param {Object} branding - The branding configuration object
 * @returns {string} The favicon URL
 */
export const getBrandingFaviconUrl = (branding) => resolveBranding(branding).faviconUrl;

/**
 * Get the application name from branding configuration.
 *
 * @param {Object} branding - The branding configuration object
 * @returns {string} The application name
 */
export const getBrandingAppName = (branding) => resolveBranding(branding).appName;

/**
 * Get the appropriate favicon URL for the current theme mode.
 * Returns the dark favicon if theme is dark and dark favicon is configured,
 * otherwise falls back to the light favicon.
 *
 * @param {Object} branding - The branding configuration object
 * @param {string} resolvedMode - The resolved theme mode ('light' or 'dark')
 * @returns {string} The favicon URL appropriate for the current theme
 */
export const getBrandingFaviconUrlForTheme = (branding, resolvedMode) => {
  const resolved = resolveBranding(branding);
  if (resolvedMode === 'dark' && resolved.darkFaviconUrl) {
    return resolved.darkFaviconUrl;
  }
  return resolved.faviconUrl;
};

/**
 * Get the appropriate topbar text color for the current theme mode.
 * Returns the configured color for the current theme if available,
 * otherwise falls back to white (#FFFFFF) for both light and dark modes.
 *
 * @param {Object} branding - The branding configuration object
 * @param {string} resolvedMode - The resolved theme mode ('light' or 'dark')
 * @returns {string} The topbar text color appropriate for the current theme
 */
export const getTopbarTextColor = (branding, resolvedMode) => {
  const resolved = resolveBranding(branding);
  if (resolvedMode === 'dark') {
    return resolved.darkTopbarTextColor || '#FFFFFF';
  }
  return resolved.lightTopbarTextColor || '#FFFFFF';
};
