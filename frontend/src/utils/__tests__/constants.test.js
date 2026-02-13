/**
 * Unit tests for theme and constant utilities
 */

import { getThemeConfig, getPrimaryColor, lightThemeBase, darkThemeBase, DEFAULT_THEME_COLOR } from '../constants';

describe('constants - theme utilities', () => {
  describe('getThemeConfig', () => {
    it('returns light theme base when mode is light', () => {
      const result = getThemeConfig('light', null);
      expect(result.palette.mode).toBe('light');
      expect(result.palette.primary.main).toBe(lightThemeBase.palette.primary.main);
    });

    it('returns dark theme base when mode is dark', () => {
      const result = getThemeConfig('dark', null);
      expect(result.palette.mode).toBe('dark');
      expect(result.palette.primary.main).toBe(darkThemeBase.palette.primary.main);
    });

    it('applies light theme overrides', () => {
      const branding = {
        lightThemeOverrides: {
          palette: {
            primary: {
              main: '#FF0000',
            },
          },
        },
      };
      const result = getThemeConfig('light', branding);
      expect(result.palette.primary.main).toBe('#FF0000');
    });

    it('applies dark theme overrides', () => {
      const branding = {
        darkThemeOverrides: {
          palette: {
            primary: {
              main: '#00FF00',
            },
          },
        },
      };
      const result = getThemeConfig('dark', branding);
      expect(result.palette.primary.main).toBe('#00FF00');
    });

    it('does not apply dark overrides to light theme', () => {
      const branding = {
        darkThemeOverrides: {
          palette: {
            primary: {
              main: '#00FF00',
            },
          },
        },
      };
      const result = getThemeConfig('light', branding);
      expect(result.palette.primary.main).toBe(lightThemeBase.palette.primary.main);
    });
  });

  describe('getPrimaryColor', () => {
    it('returns light theme primary color by default', () => {
      const result = getPrimaryColor('light', null);
      expect(result).toBe(lightThemeBase.palette.primary.main);
    });

    it('returns dark theme primary color when mode is dark', () => {
      const result = getPrimaryColor('dark', null);
      expect(result).toBe(darkThemeBase.palette.primary.main);
    });

    it('returns overridden primary color from branding', () => {
      const branding = {
        lightThemeOverrides: {
          palette: {
            primary: {
              main: '#123456',
            },
          },
        },
      };
      const result = getPrimaryColor('light', branding);
      expect(result).toBe('#123456');
    });
  });

  describe('DEFAULT_THEME_COLOR', () => {
    it('is defined and matches light theme primary color', () => {
      expect(DEFAULT_THEME_COLOR).toBeDefined();
      expect(DEFAULT_THEME_COLOR).toBe(lightThemeBase.palette.primary.main);
    });
  });
});
