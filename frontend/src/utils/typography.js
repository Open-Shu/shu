/**
 * Typography utilities for user preferences and admin branding.
 *
 * Single source of truth for the curated font family list, font size
 * scale tiers, and cascade resolvers. The backend mirrors the same
 * enums in `backend/src/shu/schemas/typography_constants.py`. A parity
 * check lives in `__tests__/typography.test.js`.
 *
 * Cascade: user_pref → brand_pref → SHIPPED_DEFAULT.
 *
 * Sizing mechanism: the ThemeContext sets `document.documentElement.style.fontSize`
 * to `${BASELINE_PERCENT * multiplier}%`. Percentage (not px) respects
 * users who configured a non-default browser font size for accessibility.
 */

export const SHIPPED_DEFAULT_FONT = 'inter';

export const BASELINE_PERCENT = 80;

export const DEFAULT_FONT_SIZE_SCALE = 'default';

/**
 * Curated font registry. Each entry maps the persisted key (matched
 * server-side against VALID_FONT_FAMILIES) to a display label and the
 * CSS font-stack used when applied to the theme.
 *
 * The CSS stack always ends in a generic family so unreachable fonts
 * (CDN failure, missing local install) degrade gracefully.
 */
export const FONT_FAMILIES = {
  'system-ui': {
    label: 'System UI',
    stack: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  },
  inter: {
    label: 'Inter',
    stack: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
  },
  roboto: {
    label: 'Roboto',
    stack: '"Roboto", "Helvetica", "Arial", sans-serif',
  },
  'space-grotesk': {
    label: 'Space Grotesk',
    stack: '"Space Grotesk", "Inter", "Helvetica", "Arial", sans-serif',
  },
  'atkinson-hyperlegible': {
    label: 'Atkinson Hyperlegible',
    stack: '"Atkinson Hyperlegible", "Inter", "Helvetica", "Arial", sans-serif',
  },
  lexend: {
    label: 'Lexend',
    stack: '"Lexend", "Inter", "Helvetica", "Arial", sans-serif',
  },
};

export const VALID_FONT_FAMILIES = Object.keys(FONT_FAMILIES);

/**
 * Font size scale tiers. Multipliers apply on top of BASELINE_PERCENT.
 * `default` (1.0) maps to the new 80%-of-browser-default baseline.
 * `xl` (1.3) takes a user back above the pre-feature baseline.
 */
export const FONT_SIZE_SCALES = {
  xs: { label: 'XS', multiplier: 0.85 },
  small: { label: 'Small', multiplier: 0.95 },
  default: { label: 'Default', multiplier: 1.0 },
  large: { label: 'Large', multiplier: 1.15 },
  xl: { label: 'XL', multiplier: 1.3 },
};

export const VALID_FONT_SIZE_SCALES = Object.keys(FONT_SIZE_SCALES);

/**
 * Resolve the effective body font key via the cascade.
 *
 * @param {string | null | undefined} userPref - user_preferences.font_family
 * @param {string | null | undefined} brandPref - branding.brand_font_family
 * @returns {string} a key from FONT_FAMILIES; falls back to SHIPPED_DEFAULT_FONT
 */
export const resolveFontFamily = (userPref, brandPref) => {
  if (userPref && FONT_FAMILIES[userPref]) {
    return userPref;
  }
  if (brandPref && FONT_FAMILIES[brandPref]) {
    return brandPref;
  }
  return SHIPPED_DEFAULT_FONT;
};

/**
 * Resolve the effective heading font key. Headings are admin-only — no
 * per-user override (yet). Falls back to SHIPPED_DEFAULT_FONT.
 *
 * @param {string | null | undefined} brandPref - branding.brand_heading_font_family
 * @returns {string} a key from FONT_FAMILIES
 */
export const resolveHeadingFontFamily = (brandPref) => {
  if (brandPref && FONT_FAMILIES[brandPref]) {
    return brandPref;
  }
  return SHIPPED_DEFAULT_FONT;
};

/**
 * Resolve the effective font size scale tier. Falls back to "default"
 * (1.0× — equal to the new 20%-smaller baseline).
 *
 * @param {string | null | undefined} userPref - user_preferences.font_size_scale
 * @returns {string} a key from FONT_SIZE_SCALES
 */
export const resolveFontSizeScale = (userPref) => {
  if (userPref && FONT_SIZE_SCALES[userPref]) {
    return userPref;
  }
  return DEFAULT_FONT_SIZE_SCALE;
};

/**
 * Compute the CSS percentage to apply as `document.documentElement.style.fontSize`.
 * Returns a string like "80%" (default tier) or "104%" (xl tier).
 *
 * @param {string | null | undefined} scaleKey - a key from FONT_SIZE_SCALES (resolved)
 * @returns {string} the CSS percentage value
 */
export const computeRootFontSizePercent = (scaleKey) => {
  const tier = FONT_SIZE_SCALES[scaleKey] || FONT_SIZE_SCALES[DEFAULT_FONT_SIZE_SCALE];
  return `${BASELINE_PERCENT * tier.multiplier}%`;
};

/**
 * Look up the CSS font-stack for a resolved font key. Falls back to the
 * shipped default's stack if the key is unknown.
 *
 * @param {string} fontKey - a key from FONT_FAMILIES
 * @returns {string} a CSS font-family stack
 */
export const getFontStack = (fontKey) => {
  const entry = FONT_FAMILIES[fontKey] || FONT_FAMILIES[SHIPPED_DEFAULT_FONT];
  return entry.stack;
};
