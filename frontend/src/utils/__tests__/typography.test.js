/**
 * Unit tests for typography cascade resolvers and the curated font
 * registry. The "parity" test catches drift between this file and the
 * backend `schemas/typography_constants.py` — that file MUST stay in
 * sync, otherwise the validator and the frontend disagree about what's
 * a legal value.
 */

import {
  BASELINE_PERCENT,
  DEFAULT_FONT_SIZE_SCALE,
  FONT_FAMILIES,
  FONT_SIZE_SCALES,
  SHIPPED_DEFAULT_FONT,
  VALID_FONT_FAMILIES,
  VALID_FONT_SIZE_SCALES,
  computeRootFontSizePercent,
  getFontStack,
  resolveFontFamily,
  resolveFontSizeScale,
  resolveHeadingFontFamily,
} from '../typography';

describe('typography registry shape', () => {
  it('every FONT_FAMILIES entry has a label and a stack ending in a generic family', () => {
    Object.entries(FONT_FAMILIES).forEach(([key, def]) => {
      expect(def.label).toBeTruthy();
      expect(def.stack).toMatch(/sans-serif$/);
      expect(key).toBe(key.toLowerCase());
    });
  });

  it('SHIPPED_DEFAULT_FONT is a key of FONT_FAMILIES', () => {
    expect(FONT_FAMILIES[SHIPPED_DEFAULT_FONT]).toBeDefined();
  });

  it('DEFAULT_FONT_SIZE_SCALE is a key of FONT_SIZE_SCALES with multiplier 1.0', () => {
    expect(FONT_SIZE_SCALES[DEFAULT_FONT_SIZE_SCALE]).toBeDefined();
    expect(FONT_SIZE_SCALES[DEFAULT_FONT_SIZE_SCALE].multiplier).toBe(1.0);
  });

  it('BASELINE_PERCENT is 80 (20% smaller than the browser default)', () => {
    expect(BASELINE_PERCENT).toBe(80);
  });

  // Backend/frontend parity. If this fails, update
  // backend/src/shu/schemas/typography_constants.py OR
  // frontend/src/utils/typography.js so both lists agree.
  it('VALID_FONT_FAMILIES matches the curated set the backend enforces', () => {
    expect([...VALID_FONT_FAMILIES].sort()).toEqual(
      ['system-ui', 'inter', 'roboto', 'space-grotesk', 'atkinson-hyperlegible', 'lexend'].sort()
    );
  });

  it('VALID_FONT_SIZE_SCALES matches the curated set the backend enforces', () => {
    expect([...VALID_FONT_SIZE_SCALES].sort()).toEqual(['xs', 'small', 'default', 'large', 'xl'].sort());
  });
});

describe('resolveFontFamily cascade', () => {
  it('returns the shipped default when both user and brand prefs are null', () => {
    expect(resolveFontFamily(null, null)).toBe(SHIPPED_DEFAULT_FONT);
  });

  it('returns the brand pref when user is null and brand is set', () => {
    expect(resolveFontFamily(null, 'space-grotesk')).toBe('space-grotesk');
  });

  it('returns the user pref when both are set (user wins)', () => {
    expect(resolveFontFamily('atkinson-hyperlegible', 'space-grotesk')).toBe('atkinson-hyperlegible');
  });

  it('returns the user pref when brand is null', () => {
    expect(resolveFontFamily('lexend', null)).toBe('lexend');
  });

  it('ignores unknown user pref and falls through to brand', () => {
    expect(resolveFontFamily('comic-sans', 'roboto')).toBe('roboto');
  });

  it('ignores unknown user and brand prefs and returns the shipped default', () => {
    expect(resolveFontFamily('comic-sans', 'wingdings')).toBe(SHIPPED_DEFAULT_FONT);
  });

  it('treats empty string as "not set"', () => {
    expect(resolveFontFamily('', 'roboto')).toBe('roboto');
    expect(resolveFontFamily('', '')).toBe(SHIPPED_DEFAULT_FONT);
  });
});

describe('resolveHeadingFontFamily cascade', () => {
  it('returns the shipped default when nothing is set', () => {
    expect(resolveHeadingFontFamily(null, null, null)).toBe(SHIPPED_DEFAULT_FONT);
  });

  it('returns the admin heading pref when set, regardless of user/brand body', () => {
    expect(resolveHeadingFontFamily('space-grotesk', 'atkinson-hyperlegible', 'lexend')).toBe('space-grotesk');
  });

  it('falls back to user body pref when no admin heading is set', () => {
    expect(resolveHeadingFontFamily(null, 'atkinson-hyperlegible', null)).toBe('atkinson-hyperlegible');
  });

  it('user body beats brand body for heading fallback (matches body cascade)', () => {
    expect(resolveHeadingFontFamily(null, 'atkinson-hyperlegible', 'space-grotesk')).toBe('atkinson-hyperlegible');
  });

  it('falls back to brand body when no admin heading and no user pick', () => {
    expect(resolveHeadingFontFamily(null, null, 'space-grotesk')).toBe('space-grotesk');
  });

  it('ignores an unknown brand-heading pref and falls through to the body cascade', () => {
    expect(resolveHeadingFontFamily('wingdings', 'atkinson-hyperlegible', null)).toBe('atkinson-hyperlegible');
  });

  it('falls back to shipped default when everything in the cascade is unknown or null', () => {
    expect(resolveHeadingFontFamily('wingdings', 'comic-sans', null)).toBe(SHIPPED_DEFAULT_FONT);
  });
});

describe('resolveFontSizeScale', () => {
  it('returns "default" when user pref is null', () => {
    expect(resolveFontSizeScale(null)).toBe('default');
  });

  it('returns each curated tier when set', () => {
    VALID_FONT_SIZE_SCALES.forEach((key) => {
      expect(resolveFontSizeScale(key)).toBe(key);
    });
  });

  it('ignores an unknown tier and falls back to "default"', () => {
    expect(resolveFontSizeScale('huge')).toBe('default');
  });
});

describe('computeRootFontSizePercent', () => {
  it('returns "80%" for the default tier', () => {
    expect(computeRootFontSizePercent('default')).toBe('80%');
  });

  it('scales from the baseline, not from 100%', () => {
    // small = 0.95 → 80 * 0.95 = 76%
    expect(computeRootFontSizePercent('small')).toBe('76%');
    // xl = 1.3 → 80 * 1.3 = 104%
    expect(computeRootFontSizePercent('xl')).toBe('104%');
  });

  it('falls back to the default tier for unknown input', () => {
    expect(computeRootFontSizePercent('huge')).toBe('80%');
    expect(computeRootFontSizePercent(null)).toBe('80%');
  });
});

describe('getFontStack', () => {
  it('returns the registered stack for a known key', () => {
    expect(getFontStack('inter')).toBe(FONT_FAMILIES.inter.stack);
  });

  it('falls back to the shipped default for an unknown key', () => {
    expect(getFontStack('wingdings')).toBe(FONT_FAMILIES[SHIPPED_DEFAULT_FONT].stack);
  });
});
