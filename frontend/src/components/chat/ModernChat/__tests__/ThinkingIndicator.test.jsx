import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ThinkingIndicator from '../ThinkingIndicator';
import { DEFAULT_POOL, RAG_POOL, PLUGIN_POOL } from '../utils/thinkingPhrases';

// Replace the SVG-heavy FeatherIcon with a marker so we can assert
// "the feather is rendered" without parsing path data.
vi.mock('../FeatherIcon', () => ({
  default: ({ sx: _sx, ...rest }) => <div data-testid="feather-icon" {...rest} />,
}));

const TestWrapper = ({ children }) => {
  const theme = createTheme();
  return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
};

const mockMatchMedia = (matches) => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query) => ({
      matches,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  );
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ThinkingIndicator — pool selection', () => {
  beforeEach(() => {
    mockMatchMedia(false);
  });

  it('renders the feather and a verb from the default pool when thinkingPool is undefined', () => {
    render(<ThinkingIndicator message={{}} />, { wrapper: TestWrapper });
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();
    const defaultWords = screen.queryAllByText((content) => DEFAULT_POOL.includes(content));
    expect(defaultWords.length).toBeGreaterThan(0);
  });

  it('renders a verb from the RAG pool when thinkingPool is "rag"', () => {
    render(<ThinkingIndicator message={{ thinkingPool: 'rag' }} />, { wrapper: TestWrapper });
    const ragWords = screen.queryAllByText((content) => RAG_POOL.includes(content));
    expect(ragWords.length).toBeGreaterThan(0);
  });

  it('renders a verb from the plugin pool when thinkingPool is "plugin"', () => {
    render(<ThinkingIndicator message={{ thinkingPool: 'plugin' }} />, { wrapper: TestWrapper });
    const pluginWords = screen.queryAllByText((content) => PLUGIN_POOL.includes(content));
    expect(pluginWords.length).toBeGreaterThan(0);
  });
});

describe('ThinkingIndicator — reduced-motion fallback', () => {
  beforeEach(() => {
    mockMatchMedia(true);
  });

  it('renders the static "Thinking…" label and the feather', () => {
    render(<ThinkingIndicator message={{ thinkingPool: 'rag' }} />, { wrapper: TestWrapper });
    expect(screen.getByText('Thinking…')).toBeInTheDocument();
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();
  });

  it('does not render any rotating-pool verbs (no animated word)', () => {
    render(<ThinkingIndicator message={{ thinkingPool: 'rag' }} />, { wrapper: TestWrapper });
    const ragWords = screen.queryAllByText((content) => RAG_POOL.includes(content));
    expect(ragWords).toHaveLength(0);
  });

  it('does not render the longest-word ghost element used for layout stability', () => {
    render(<ThinkingIndicator message={{}} />, { wrapper: TestWrapper });
    // The ghost is the globally-longest verb ("Coordinating") used in the
    // animated layout only. Its absence here confirms the reduced-motion
    // branch is taken.
    expect(screen.queryByText('Coordinating')).not.toBeInTheDocument();
  });
});

describe('ThinkingIndicator — lifecycle cleanup', () => {
  beforeEach(() => {
    mockMatchMedia(false);
  });

  it('clears the word-rotation interval when unmounted', () => {
    const clearSpy = vi.spyOn(global, 'clearInterval');
    const { unmount } = render(<ThinkingIndicator message={{}} />, { wrapper: TestWrapper });

    const callsBefore = clearSpy.mock.calls.length;
    unmount();
    expect(clearSpy.mock.calls.length).toBeGreaterThan(callsBefore);

    clearSpy.mockRestore();
  });
});
