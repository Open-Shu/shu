/**
 * Page-level tests for CostUsagePage. Mocks the useUsageData hook so each
 * test can exercise a specific data state without driving real React Query.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';

const mockRefetch = vi.fn();

vi.mock('../../hooks/useUsageData', () => ({
  useUsageData: vi.fn(),
}));

vi.mock('../../components/billing/SubscriptionStrip', () => ({
  default: ({ subscriptionQuery }) => (
    <div data-testid="subscription-strip-mock">
      strip:{subscriptionQuery?.isLoading ? 'loading' : subscriptionQuery?.isError ? 'error' : 'ok'}
    </div>
  ),
}));

import { useUsageData } from '../../hooks/useUsageData';
import CostUsagePage from '../CostUsagePage';

const renderPage = () => {
  const theme = createTheme();
  return render(
    <ThemeProvider theme={theme}>
      <CostUsagePage />
    </ThemeProvider>
  );
};

const okQuery = (data, overrides = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  isError: false,
  isSuccess: true,
  dataUpdatedAt: 0,
  ...overrides,
});

const baseHookReturn = (overrides = {}) => ({
  usage: okQuery({ total_cost_usd: 0, by_model: [] }),
  subscription: okQuery({ current_period_unknown: true }),
  modelsMap: new Map(),
  modelsLoading: false,
  refetch: mockRefetch,
  lastUpdatedAt: 0,
  ...overrides,
});

describe('CostUsagePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the page title', () => {
    useUsageData.mockReturnValue(baseHookReturn());
    renderPage();
    expect(screen.getByRole('heading', { name: 'Cost & Usage' })).toBeInTheDocument();
  });

  it('renders both section headings', () => {
    useUsageData.mockReturnValue(baseHookReturn());
    renderPage();
    expect(screen.getByText('Summary')).toBeInTheDocument();
    expect(screen.getByText('Cost by Model')).toBeInTheDocument();
  });

  it('renders "No active billing period" subtitle when current_period_unknown', () => {
    useUsageData.mockReturnValue(baseHookReturn({ usage: okQuery({ current_period_unknown: true }) }));
    renderPage();
    expect(screen.getByText('No active billing period')).toBeInTheDocument();
  });

  it('renders the formatted billing period subtitle when known', () => {
    useUsageData.mockReturnValue(
      baseHookReturn({
        usage: okQuery({
          period_start: '2026-04-15T12:00:00Z',
          period_end: '2026-05-15T12:00:00Z',
          total_cost_usd: 10,
          by_model: [],
        }),
      })
    );
    renderPage();
    // The subtitle includes "Current billing period:" plus the formatted range.
    expect(screen.getByText(/Current billing period:/)).toBeInTheDocument();
  });

  it('shows a skeleton subtitle while usage is loading', () => {
    useUsageData.mockReturnValue(
      baseHookReturn({
        usage: { ...okQuery(undefined), isLoading: true, isSuccess: false },
      })
    );
    const { container } = renderPage();
    // The header subtitle should render a skeleton while waiting.
    expect(container.querySelector('.MuiSkeleton-root')).toBeTruthy();
  });

  it('renders the inline retry Alert in both Summary and Cost by Model on usage error', () => {
    useUsageData.mockReturnValue(
      baseHookReturn({
        usage: { ...okQuery(undefined), isError: true, isSuccess: false },
      })
    );
    renderPage();
    const retryButtons = screen.getAllByRole('button', { name: /retry/i });
    // One in Summary, one in Cost by Model.
    expect(retryButtons.length).toBe(2);
    const errorMessages = screen.getAllByText(/We couldn.t load usage data/i);
    expect(errorMessages.length).toBe(2);
  });

  it('renders KPI tiles and Cost by Model when usage data is available', () => {
    // Round numbers chosen so the post-markup math renders cleanly:
    // total_cost_usd $100 × 1.3 = $130.00 in the Usage Cost tile.
    useUsageData.mockReturnValue(
      baseHookReturn({
        usage: okQuery({
          total_cost_usd: 100,
          total_input_tokens: 1_250_000,
          total_output_tokens: 850_000,
          period_start: '2026-04-15T12:00:00Z',
          period_end: '2026-05-15T12:00:00Z',
          by_model: [
            {
              model_id: 'm-1',
              cost_usd: 22.5,
              input_tokens: 500_000,
              output_tokens: 350_000,
              request_count: 324,
            },
          ],
        }),
        modelsMap: new Map([['m-1', { display_name: 'Claude Haiku 4.5', provider_name: 'anthropic' }]]),
      })
    );
    renderPage();
    // Usage Cost tile renders the post-markup billed cost.
    expect(screen.getByText('$130.00')).toBeInTheDocument();
    // Cost by Model table renders raw per-row cost (markup not applied per row).
    expect(screen.getByText('Claude Haiku 4.5')).toBeInTheDocument();
    expect(screen.getByText('$22.50')).toBeInTheDocument();
  });

  it('clicking the refresh button calls refetch', () => {
    useUsageData.mockReturnValue(baseHookReturn());
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /refresh usage data/i }));
    expect(mockRefetch).toHaveBeenCalledTimes(1);
  });

  it('refresh button is disabled while usage isFetching', () => {
    useUsageData.mockReturnValue(
      baseHookReturn({ usage: okQuery({ total_cost_usd: 0, by_model: [] }, { isFetching: true }) })
    );
    renderPage();
    expect(screen.getByRole('button', { name: /refresh usage data/i })).toBeDisabled();
  });

  it('renders "Last updated never" when no data has been fetched', () => {
    useUsageData.mockReturnValue(baseHookReturn({ lastUpdatedAt: 0 }));
    renderPage();
    expect(screen.getByText(/Last updated never/i)).toBeInTheDocument();
  });

  it('passes the subscription query through to SubscriptionStrip', () => {
    useUsageData.mockReturnValue(baseHookReturn());
    renderPage();
    expect(screen.getByTestId('subscription-strip-mock')).toHaveTextContent('strip:ok');
  });
});
