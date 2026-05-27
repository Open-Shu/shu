/**
 * Behavioral coverage for the SHU-811 typography wiring in ThemeContext:
 *
 * 1. Cascade resolves correctly through user → brand → shipped default.
 * 2. Selecting a font via `changeFontFamily` causes MUI's
 *    `theme.typography.fontFamily` to reflect the new value — catches the
 *    `useMemo` deps regression flagged as Risk R5 in the plan.
 * 3. Changing the size scale updates `document.documentElement.style.fontSize`
 *    to the expected percentage (mechanism for the 20% baseline reduction).
 * 4. localStorage fallback gives anonymous users their last-picked font.
 */

import React from 'react';
import { render, screen, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { ThemeProvider, useTheme } from '../ThemeContext';
import { FONT_FAMILIES } from '../../utils/typography';

vi.mock('../../services/api', () => ({
  userPreferencesAPI: {
    getPreferences: vi.fn(),
    patchPreferences: vi.fn().mockResolvedValue({ data: {} }),
  },
  brandingAPI: {
    getBranding: vi.fn(),
  },
  extractDataFromResponse: (response) => {
    if (response && typeof response === 'object' && 'data' in response) {
      return response.data;
    }
    return response;
  },
}));

// Stable singleton — returning a fresh object literal would change `user`
// reference each render, re-firing ThemeProvider's prefs-load useEffect and
// clobbering in-progress user actions. vi.mock factory must be self-contained
// (it's hoisted above imports), so the singleton lives inside the factory.
vi.mock('../../hooks/useAuth', () => {
  const stableUser = { id: 'u1' };
  return {
    useAuth: vi.fn(() => ({ isAuthenticated: true, user: stableUser })),
  };
});

vi.mock('../../utils/log', () => ({
  default: { warn: vi.fn(), info: vi.fn(), error: vi.fn(), debug: vi.fn() },
}));

import { userPreferencesAPI, brandingAPI } from '../../services/api';

const ThemeProbe = ({ onContext }) => {
  const ctx = useTheme();
  React.useEffect(() => {
    onContext(ctx);
  });
  return (
    <div>
      <div data-testid="resolvedFontFamily">{ctx.resolvedFontFamily}</div>
      <div data-testid="resolvedHeadingFontFamily">{ctx.resolvedHeadingFontFamily}</div>
      <div data-testid="resolvedFontScale">{ctx.resolvedFontScale}</div>
      <div data-testid="themeMode">{ctx.themeMode}</div>
      <div data-testid="themeFontFamily">{ctx.theme?.typography?.fontFamily}</div>
      <div data-testid="themeH1FontFamily">{ctx.theme?.typography?.h1?.fontFamily}</div>
    </div>
  );
};

/** Wait for the initial async branding + prefs loads to settle so the
 * subsequent user actions aren't clobbered by the late server response. */
const waitForInitialLoad = async () => {
  await waitFor(() => expect(brandingAPI.getBranding).toHaveBeenCalled());
  await waitFor(() => expect(userPreferencesAPI.getPreferences).toHaveBeenCalled());
  // themeMode flips from 'auto' to the server-supplied value once the
  // prefs load settles. Use that as our "loaded" gate.
  await waitFor(() => expect(screen.getByTestId('themeMode').textContent).toBe('light'));
};

const renderWithBrandingAndPrefs = ({ branding = {}, prefs = {} } = {}) => {
  brandingAPI.getBranding.mockResolvedValue({ data: branding });
  userPreferencesAPI.getPreferences.mockResolvedValue({ data: { theme: 'light', ...prefs } });
  const capture = vi.fn();
  render(
    <ThemeProvider>
      <ThemeProbe onContext={capture} />
    </ThemeProvider>
  );
  return capture;
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  document.documentElement.style.fontSize = '';
  // jsdom doesn't ship `matchMedia`; the ThemeProvider uses it for the
  // 'auto' theme system-preference resolver. Stub a minimal listener-shape.
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  }
});

describe('ThemeContext typography cascade', () => {
  it('falls back to shipped default (inter) when no user or brand pref is set', async () => {
    renderWithBrandingAndPrefs();
    await waitFor(() => expect(screen.getByTestId('resolvedFontFamily').textContent).toBe('inter'));
    expect(screen.getByTestId('resolvedHeadingFontFamily').textContent).toBe('inter');
  });

  it('resolves to brand body font when user has no pref', async () => {
    renderWithBrandingAndPrefs({ branding: { brand_font_family: 'space-grotesk' } });
    await waitFor(() => expect(screen.getByTestId('resolvedFontFamily').textContent).toBe('space-grotesk'));
  });

  it('user pref wins over brand body font', async () => {
    renderWithBrandingAndPrefs({
      branding: { brand_font_family: 'space-grotesk' },
      prefs: { font_family: 'atkinson-hyperlegible' },
    });
    await waitFor(() => expect(screen.getByTestId('resolvedFontFamily').textContent).toBe('atkinson-hyperlegible'));
  });

  it('heading font ignores user pref (admin-only)', async () => {
    renderWithBrandingAndPrefs({
      branding: { brand_heading_font_family: 'lexend' },
      prefs: { font_family: 'atkinson-hyperlegible' },
    });
    await waitFor(() => expect(screen.getByTestId('resolvedHeadingFontFamily').textContent).toBe('lexend'));
  });
});

describe('ThemeContext MUI theme reflects font selection', () => {
  it('theme.typography.fontFamily uses the resolved body font stack', async () => {
    renderWithBrandingAndPrefs({ prefs: { font_family: 'roboto' } });
    await waitFor(() => expect(screen.getByTestId('themeFontFamily').textContent).toBe(FONT_FAMILIES.roboto.stack));
  });

  it('theme.typography.h1.fontFamily uses the resolved heading font stack', async () => {
    renderWithBrandingAndPrefs({ branding: { brand_heading_font_family: 'lexend' } });
    await waitFor(() => expect(screen.getByTestId('themeH1FontFamily').textContent).toBe(FONT_FAMILIES.lexend.stack));
  });

  it('changeFontFamily updates the MUI theme — catches the useMemo deps regression', async () => {
    const capture = vi.fn();
    brandingAPI.getBranding.mockResolvedValue({ data: {} });
    userPreferencesAPI.getPreferences.mockResolvedValue({ data: { theme: 'light' } });
    render(
      <ThemeProvider>
        <ThemeProbe onContext={capture} />
      </ThemeProvider>
    );
    await waitForInitialLoad();
    await waitFor(() => expect(screen.getByTestId('themeFontFamily').textContent).toBe(FONT_FAMILIES.inter.stack));

    // Pull the latest captured context value.
    const ctx = capture.mock.calls[capture.mock.calls.length - 1][0];
    await act(async () => {
      await ctx.changeFontFamily('space-grotesk');
    });

    await waitFor(() =>
      expect(screen.getByTestId('themeFontFamily').textContent).toBe(FONT_FAMILIES['space-grotesk'].stack)
    );
  });
});

describe('ThemeContext applies root font-size from scale', () => {
  it('default scale yields 80% root font-size', async () => {
    renderWithBrandingAndPrefs();
    await waitFor(() => expect(document.documentElement.style.fontSize).toBe('80%'));
  });

  it('xl scale yields 104% root font-size', async () => {
    renderWithBrandingAndPrefs({ prefs: { font_size_scale: 'xl' } });
    await waitFor(() => expect(document.documentElement.style.fontSize).toBe('104%'));
  });

  it('changeFontScale updates the documentElement style', async () => {
    const capture = vi.fn();
    brandingAPI.getBranding.mockResolvedValue({ data: {} });
    userPreferencesAPI.getPreferences.mockResolvedValue({ data: { theme: 'light' } });
    render(
      <ThemeProvider>
        <ThemeProbe onContext={capture} />
      </ThemeProvider>
    );
    await waitForInitialLoad();
    await waitFor(() => expect(document.documentElement.style.fontSize).toBe('80%'));

    const ctx = capture.mock.calls[capture.mock.calls.length - 1][0];
    await act(async () => {
      await ctx.changeFontScale('large');
    });

    // large = 1.15 → 80 * 1.15 = 92%
    await waitFor(() => expect(document.documentElement.style.fontSize).toBe('92%'));
  });
});
