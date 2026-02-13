/**
 * Unit tests for branding utility functions
 */

import {
  resolveBranding,
  getBrandingFaviconUrl,
  getBrandingAppName,
  getTopbarTextColor,
  defaultBranding,
} from '../brandingUtils';

describe('brandingUtils', () => {
  describe('resolveBranding', () => {
    it('returns default branding when no branding provided', () => {
      const result = resolveBranding(null);
      expect(result).toEqual(defaultBranding);
    });

    it('resolves camelCase field names', () => {
      const branding = {
        appName: 'Test App',
        faviconUrl: '/test-favicon.png',
      };
      const result = resolveBranding(branding);
      expect(result.appName).toBe('Test App');
      expect(result.faviconUrl).toBe('/test-favicon.png');
    });

    it('resolves snake_case field names', () => {
      const branding = {
        app_name: 'Test App',
        favicon_url: '/test-favicon.png',
      };
      const result = resolveBranding(branding);
      expect(result.appName).toBe('Test App');
      expect(result.faviconUrl).toBe('/test-favicon.png');
    });

    it('uses default values for missing fields', () => {
      const branding = {
        appName: 'Test App',
      };
      const result = resolveBranding(branding);
      expect(result.appName).toBe('Test App');
      expect(result.faviconUrl).toBe(defaultBranding.faviconUrl);
    });

    it('handles all branding fields', () => {
      const branding = {
        appName: 'Test App',
        faviconUrl: '/favicon.ico',
        darkFaviconUrl: '/dark-favicon.ico',
        lightTopbarTextColor: '#000000',
        darkTopbarTextColor: '#FFFFFF',
        lightThemeOverrides: { palette: { primary: { main: '#123456' } } },
        darkThemeOverrides: { palette: { primary: { main: '#654321' } } },
        updatedAt: '2024-01-01T00:00:00Z',
        updatedBy: 'admin@example.com',
      };
      const result = resolveBranding(branding);
      expect(result.appName).toBe('Test App');
      expect(result.faviconUrl).toBe('/favicon.ico');
      expect(result.darkFaviconUrl).toBe('/dark-favicon.ico');
      expect(result.lightTopbarTextColor).toBe('#000000');
      expect(result.darkTopbarTextColor).toBe('#FFFFFF');
      expect(result.lightThemeOverrides).toEqual({ palette: { primary: { main: '#123456' } } });
      expect(result.darkThemeOverrides).toEqual({ palette: { primary: { main: '#654321' } } });
      expect(result.updatedAt).toBe('2024-01-01T00:00:00Z');
      expect(result.updatedBy).toBe('admin@example.com');
    });
  });

  describe('getBrandingFaviconUrl', () => {
    it('returns favicon URL from branding', () => {
      const branding = { faviconUrl: '/custom-favicon.ico' };
      const result = getBrandingFaviconUrl(branding);
      expect(result).toBe('/custom-favicon.ico');
    });

    it('returns default favicon URL when not configured', () => {
      const result = getBrandingFaviconUrl({});
      expect(result).toBe(defaultBranding.faviconUrl);
    });
  });

  describe('getBrandingAppName', () => {
    it('returns app name from branding', () => {
      const branding = { appName: 'Custom App' };
      const result = getBrandingAppName(branding);
      expect(result).toBe('Custom App');
    });

    it('returns default app name when not configured', () => {
      const result = getBrandingAppName({});
      expect(result).toBe(defaultBranding.appName);
    });
  });

  describe('getTopbarTextColor', () => {
    it('returns configured light color when theme is light', () => {
      const branding = {
        lightTopbarTextColor: '#1A202C',
        darkTopbarTextColor: '#F0F6FC',
      };
      const result = getTopbarTextColor(branding, 'light');
      expect(result).toBe('#1A202C');
    });

    it('returns configured dark color when theme is dark', () => {
      const branding = {
        lightTopbarTextColor: '#1A202C',
        darkTopbarTextColor: '#F0F6FC',
      };
      const result = getTopbarTextColor(branding, 'dark');
      expect(result).toBe('#F0F6FC');
    });

    it('falls back to #FFFFFF when theme is light and no color is configured', () => {
      const branding = {
        lightTopbarTextColor: null,
      };
      const result = getTopbarTextColor(branding, 'light');
      expect(result).toBe('#FFFFFF');
    });

    it('falls back to #FFFFFF when theme is dark and no color is configured', () => {
      const branding = {
        darkTopbarTextColor: null,
      };
      const result = getTopbarTextColor(branding, 'dark');
      expect(result).toBe('#FFFFFF');
    });

    it('falls back to #FFFFFF when theme is light and color is undefined', () => {
      const branding = {};
      const result = getTopbarTextColor(branding, 'light');
      expect(result).toBe('#FFFFFF');
    });

    it('falls back to #FFFFFF when theme is dark and color is undefined', () => {
      const branding = {};
      const result = getTopbarTextColor(branding, 'dark');
      expect(result).toBe('#FFFFFF');
    });

    it('handles snake_case field names for light theme', () => {
      const branding = {
        light_topbar_text_color: '#123456',
      };
      const result = getTopbarTextColor(branding, 'light');
      expect(result).toBe('#123456');
    });

    it('handles snake_case field names for dark theme', () => {
      const branding = {
        dark_topbar_text_color: '#ABCDEF',
      };
      const result = getTopbarTextColor(branding, 'dark');
      expect(result).toBe('#ABCDEF');
    });

    it('handles null branding object for light theme', () => {
      const result = getTopbarTextColor(null, 'light');
      expect(result).toBe('#FFFFFF');
    });

    it('handles null branding object for dark theme', () => {
      const result = getTopbarTextColor(null, 'dark');
      expect(result).toBe('#FFFFFF');
    });

    it('handles empty branding object for light theme', () => {
      const result = getTopbarTextColor({}, 'light');
      expect(result).toBe('#FFFFFF');
    });

    it('handles empty branding object for dark theme', () => {
      const result = getTopbarTextColor({}, 'dark');
      expect(result).toBe('#FFFFFF');
    });
  });
});
