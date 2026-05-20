import React from 'react';
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { BillingStatusProvider, useBillingStatus } from './BillingStatusContext';
import { billingAPI } from '../services/api';
import { useAuth } from '../hooks/useAuth';

vi.mock('../services/api', () => ({
  billingAPI: {
    getSubscription: vi.fn(),
  },
  // Real implementation — pure function, safe to use in tests so the context
  // exercises the same envelope-unwrap logic as production.
  extractDataFromResponse: (response) => {
    if (response && typeof response === 'object' && 'data' in response) {
      const firstData = response.data;
      if (firstData && typeof firstData === 'object' && 'data' in firstData) {
        return firstData.data;
      }
      return firstData;
    }
    return response;
  },
}));

// Default to authenticated so existing tests don't all need to opt in. The
// auth-gating test below overrides to false explicitly.
vi.mock('../hooks/useAuth', () => ({
  useAuth: vi.fn(() => ({ isAuthenticated: true })),
}));

// Silence the warn() call in the swallowed-error path so test output stays clean.
vi.mock('../utils/log', () => ({
  default: {
    warn: vi.fn(),
    info: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
  },
}));

const HEALTHY_RESPONSE = {
  data: {
    user_count: 1,
    user_limit: 5,
    user_limit_enforcement: 'soft',
    at_user_limit: false,
    payment_failed_at: null,
    payment_grace_days: 0,
    grace_deadline: null,
    service_paused: false,
  },
};

const PAUSED_RESPONSE = {
  data: {
    user_count: 1,
    user_limit: 5,
    user_limit_enforcement: 'soft',
    at_user_limit: false,
    payment_failed_at: '2026-04-15T00:00:00Z',
    payment_grace_days: 14,
    grace_deadline: '2026-04-29T00:00:00Z',
    service_paused: true,
  },
};

const StatusProbe = () => {
  const { paymentFailedAt, graceDeadline, servicePaused, loading } = useBillingStatus();
  return (
    <div>
      <div data-testid="loading">{String(loading)}</div>
      <div data-testid="paymentFailedAt">{String(paymentFailedAt)}</div>
      <div data-testid="graceDeadline">{String(graceDeadline)}</div>
      <div data-testid="servicePaused">{String(servicePaused)}</div>
    </div>
  );
};

const renderProvider = () =>
  render(
    <BillingStatusProvider>
      <StatusProbe />
    </BillingStatusProvider>
  );

describe('BillingStatusContext', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    billingAPI.getSubscription.mockReset();
    useAuth.mockReturnValue({ isAuthenticated: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders children with healthy initial state before first fetch resolves', () => {
    // Pending promise never resolves during this assertion window — we want to
    // observe the pre-fetch defaults the provider exposes synchronously on mount.
    billingAPI.getSubscription.mockReturnValue(new Promise(() => {}));

    renderProvider();

    expect(screen.getByTestId('loading').textContent).toBe('true');
    expect(screen.getByTestId('paymentFailedAt').textContent).toBe('null');
    expect(screen.getByTestId('graceDeadline').textContent).toBe('null');
    expect(screen.getByTestId('servicePaused').textContent).toBe('false');
  });

  it('updates context value when fetch returns service_paused=true', async () => {
    billingAPI.getSubscription.mockResolvedValue(PAUSED_RESPONSE);

    renderProvider();

    await waitFor(() => {
      expect(screen.getByTestId('loading').textContent).toBe('false');
    });

    expect(screen.getByTestId('paymentFailedAt').textContent).toBe('2026-04-15T00:00:00Z');
    expect(screen.getByTestId('graceDeadline').textContent).toBe('2026-04-29T00:00:00Z');
    expect(screen.getByTestId('servicePaused').textContent).toBe('true');
  });

  it('re-fetches every 60 seconds via the polling interval', async () => {
    billingAPI.getSubscription.mockResolvedValue(HEALTHY_RESPONSE);

    renderProvider();

    // Wait for the mount-fetch to fully commit (loading flips to false) before
    // driving the timer — otherwise the post-fetch setState lands outside act().
    await waitFor(() => {
      expect(screen.getByTestId('loading').textContent).toBe('false');
    });
    expect(billingAPI.getSubscription).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(billingAPI.getSubscription).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(billingAPI.getSubscription).toHaveBeenCalledTimes(3);
  });

  it('re-fetches when window receives a focus event', async () => {
    billingAPI.getSubscription.mockResolvedValue(HEALTHY_RESPONSE);

    renderProvider();

    await waitFor(() => {
      expect(screen.getByTestId('loading').textContent).toBe('false');
    });
    expect(billingAPI.getSubscription).toHaveBeenCalledTimes(1);

    await act(async () => {
      fireEvent.focus(window);
    });

    expect(billingAPI.getSubscription).toHaveBeenCalledTimes(2);
  });

  it('keeps last-known-healthy state and settles loading=false when fetch rejects', async () => {
    billingAPI.getSubscription.mockRejectedValue(new Error('network blip'));

    renderProvider();

    await waitFor(() => {
      expect(screen.getByTestId('loading').textContent).toBe('false');
    });

    // No error surface — banner-relevant fields stay at healthy defaults.
    expect(screen.getByTestId('paymentFailedAt').textContent).toBe('null');
    expect(screen.getByTestId('graceDeadline').textContent).toBe('null');
    expect(screen.getByTestId('servicePaused').textContent).toBe('false');
  });

  it('does not poll while unauthenticated and resumes polling on auth flip', async () => {
    // Belt-and-suspenders with the auth boundary in App.js: even if the
    // provider somehow renders without an authenticated session, no fetch
    // should fire — every call would be a guaranteed 401.
    useAuth.mockReturnValue({ isAuthenticated: false });
    billingAPI.getSubscription.mockResolvedValue(HEALTHY_RESPONSE);

    const { rerender } = renderProvider();

    await vi.advanceTimersByTimeAsync(60_000);
    fireEvent.focus(window);
    expect(billingAPI.getSubscription).not.toHaveBeenCalled();

    // Simulate login completing — the effect re-runs and polling kicks in.
    useAuth.mockReturnValue({ isAuthenticated: true });
    rerender(
      <BillingStatusProvider>
        <StatusProbe />
      </BillingStatusProvider>
    );

    await waitFor(() => {
      expect(billingAPI.getSubscription).toHaveBeenCalledTimes(1);
    });
  });

  it('parses the trial/grant wire shape into numeric context fields', async () => {
    // Backend stringifies Decimals to dodge JSON-number precision loss.
    // The context layer parses them back to Number at the boundary so banner
    // consumers don't need to remember to. This test pins that contract — a
    // future shape change (e.g., backend stops stringifying) would surface here.
    billingAPI.getSubscription.mockResolvedValue({
      data: {
        ...HEALTHY_RESPONSE.data,
        is_trial: true,
        trial_deadline: '2026-06-15T00:00:00Z',
        total_grant_amount: '50.00',
        remaining_grant_amount: '12.34',
        seat_price_usd: '20.00',
        user_count: 3,
      },
    });

    const TrialProbe = () => {
      const ctx = useBillingStatus();
      return (
        <div>
          <div data-testid="isTrial">{String(ctx.isTrial)}</div>
          <div data-testid="trialDeadline">{String(ctx.trialDeadline)}</div>
          <div data-testid="totalGrantAmount">{String(ctx.totalGrantAmount)}</div>
          <div data-testid="remainingGrantAmount">{String(ctx.remainingGrantAmount)}</div>
          <div data-testid="seatPriceUsd">{String(ctx.seatPriceUsd)}</div>
          <div data-testid="userCount">{String(ctx.userCount)}</div>
          <div data-testid="totalGrantType">{typeof ctx.totalGrantAmount}</div>
        </div>
      );
    };

    render(
      <BillingStatusProvider>
        <TrialProbe />
      </BillingStatusProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isTrial').textContent).toBe('true');
    });

    expect(screen.getByTestId('trialDeadline').textContent).toBe('2026-06-15T00:00:00Z');
    expect(screen.getByTestId('totalGrantAmount').textContent).toBe('50');
    expect(screen.getByTestId('remainingGrantAmount').textContent).toBe('12.34');
    expect(screen.getByTestId('seatPriceUsd').textContent).toBe('20');
    expect(screen.getByTestId('userCount').textContent).toBe('3');
    // Type assertion: consumers expect Number, not string. Catches a regression
    // where a future refactor accidentally passes the raw stringified value through.
    expect(screen.getByTestId('totalGrantType').textContent).toBe('number');
  });

  it('coerces malformed decimal strings to null instead of NaN', async () => {
    // Number('abc') is NaN, which silently feeds into .toFixed() in TrialBanner
    // and renders "$NaN of $NaN". parseDecimalField should clamp non-finite
    // values to null so banner consumers can treat absence and parse-failure
    // identically.
    billingAPI.getSubscription.mockResolvedValue({
      data: {
        ...HEALTHY_RESPONSE.data,
        is_trial: true,
        total_grant_amount: 'not-a-number',
        remaining_grant_amount: '',
        seat_price_usd: 'NaN',
      },
    });

    const NullProbe = () => {
      const ctx = useBillingStatus();
      return (
        <div>
          <div data-testid="total">{String(ctx.totalGrantAmount)}</div>
          <div data-testid="remaining">{String(ctx.remainingGrantAmount)}</div>
          <div data-testid="seat">{String(ctx.seatPriceUsd)}</div>
        </div>
      );
    };

    render(
      <BillingStatusProvider>
        <NullProbe />
      </BillingStatusProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('total').textContent).toBe('null');
    });
    expect(screen.getByTestId('remaining').textContent).toBe('null');
    expect(screen.getByTestId('seat').textContent).toBe('null');
  });

  it('cleans up interval and focus listener on unmount', async () => {
    billingAPI.getSubscription.mockResolvedValue(HEALTHY_RESPONSE);

    const { unmount } = renderProvider();

    // Let the mount-fetch fully commit before unmount so no setState lands on
    // an unmounted tree.
    await waitFor(() => {
      expect(screen.getByTestId('loading').textContent).toBe('false');
    });
    expect(billingAPI.getSubscription).toHaveBeenCalledTimes(1);

    unmount();

    await vi.advanceTimersByTimeAsync(60_000);
    fireEvent.focus(window);

    expect(billingAPI.getSubscription).toHaveBeenCalledTimes(1);
  });
});
