import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import StreamingFeather from '../StreamingFeather';

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

describe('StreamingFeather — initial mount', () => {
  beforeEach(() => {
    mockMatchMedia(false);
  });

  it('renders the feather when isStreaming is true', () => {
    render(<StreamingFeather isStreaming />, { wrapper: TestWrapper });
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();
  });

  it('renders nothing when isStreaming is false on mount (done message from cache)', () => {
    const { container } = render(<StreamingFeather isStreaming={false} />, { wrapper: TestWrapper });
    expect(container).toBeEmptyDOMElement();
  });
});

describe('StreamingFeather — phase machine', () => {
  beforeEach(() => {
    mockMatchMedia(false);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps the feather mounted while the settle animation runs after isStreaming flips false', () => {
    const { rerender } = render(<StreamingFeather isStreaming />, { wrapper: TestWrapper });
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();

    vi.useFakeTimers();
    rerender(<StreamingFeather isStreaming={false} />);
    // Settle is in progress — feather should still be in the DOM.
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();
  });

  it('unmounts the feather after the settle timeout completes (1000ms)', () => {
    const { rerender } = render(<StreamingFeather isStreaming />, { wrapper: TestWrapper });

    vi.useFakeTimers();
    rerender(<StreamingFeather isStreaming={false} />);
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1500);
    });

    expect(screen.queryByTestId('feather-icon')).not.toBeInTheDocument();
  });

  it('returns to the streaming phase if isStreaming flips back to true mid-settle', () => {
    const { rerender } = render(<StreamingFeather isStreaming />, { wrapper: TestWrapper });

    vi.useFakeTimers();
    rerender(<StreamingFeather isStreaming={false} />);
    // Mid-settle
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();

    rerender(<StreamingFeather isStreaming />);
    // Back to streaming — feather still here, settle timer should be cancelled.
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();

    // Advancing past the original settle window must NOT unmount the feather.
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();
  });
});

describe('StreamingFeather — reduced-motion fallback', () => {
  beforeEach(() => {
    mockMatchMedia(true);
  });

  it('skips the settle animation and unmounts immediately on isStreaming false', () => {
    const { rerender } = render(<StreamingFeather isStreaming />, { wrapper: TestWrapper });
    expect(screen.getByTestId('feather-icon')).toBeInTheDocument();

    rerender(<StreamingFeather isStreaming={false} />);
    expect(screen.queryByTestId('feather-icon')).not.toBeInTheDocument();
  });
});
