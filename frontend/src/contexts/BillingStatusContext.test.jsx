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
