/**
 * Tests for the SubscriptionStrip component and its pure view selector.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';

import SubscriptionStrip, { selectSubscriptionView } from '../SubscriptionStrip';

vi.mock('../../../services/api', async () => {
  const actual = await vi.importActual('../../../services/api');
  return {
    ...actual,
    billingAPI: {
      getPortalUrl: vi.fn(),
    },
  };
});

import { billingAPI } from '../../../services/api';

const renderStrip = (subscriptionQuery, timezone = 'UTC') => {
  const theme = createTheme();
  return render(
    <ThemeProvider theme={theme}>
      <SubscriptionStrip subscriptionQuery={subscriptionQuery} timezone={timezone} />
    </ThemeProvider>
  );
};

const okQuery = (data) => ({ data, isLoading: false, isError: false });

describe('selectSubscriptionView', () => {
  it('returns hidden when current_period_unknown', () => {
    expect(selectSubscriptionView({ current_period_unknown: true, subscription_status: 'active' })).toBe('hidden');
  });

  it('returns hidden when state is null', () => {
    expect(selectSubscriptionView(null)).toBe('hidden');
  });

  it('returns healthy for an active subscription with no payment failure and no cancel flag', () => {
    expect(
      selectSubscriptionView({
        subscription_status: 'active',
        payment_failed_at: null,
        cancel_at_period_end: false,
      })
    ).toBe('healthy');
  });

  it('returns unhealthy:payment_failed when payment_failed_at is set', () => {
    expect(
      selectSubscriptionView({
        subscription_status: 'active',
        payment_failed_at: '2026-04-25T12:00:00Z',
      })
    ).toBe('unhealthy:payment_failed');
  });

  it('returns unhealthy:past_due when status is past_due and no payment failure', () => {
    expect(selectSubscriptionView({ subscription_status: 'past_due', payment_failed_at: null })).toBe(
      'unhealthy:past_due'
    );
  });

  it('returns unhealthy:canceled when status is canceled', () => {
    expect(selectSubscriptionView({ subscription_status: 'canceled' })).toBe('unhealthy:canceled');
  });

  it('returns unhealthy:will_not_renew when active and cancel_at_period_end', () => {
    expect(
      selectSubscriptionView({
        subscription_status: 'active',
        cancel_at_period_end: true,
        payment_failed_at: null,
      })
    ).toBe('unhealthy:will_not_renew');
  });

  it('returns unhealthy:incomplete for incomplete or incomplete_expired', () => {
    expect(selectSubscriptionView({ subscription_status: 'incomplete' })).toBe('unhealthy:incomplete');
    expect(selectSubscriptionView({ subscription_status: 'incomplete_expired' })).toBe('unhealthy:incomplete');
  });

  it('payment_failed wins precedence over cancel_at_period_end when both are true', () => {
    expect(
      selectSubscriptionView({
        subscription_status: 'active',
        payment_failed_at: '2026-04-25T12:00:00Z',
        cancel_at_period_end: true,
      })
    ).toBe('unhealthy:payment_failed');
  });

  it('falls through to unhealthy:incomplete for an unknown status', () => {
    expect(selectSubscriptionView({ subscription_status: 'something_new' })).toBe('unhealthy:incomplete');
  });
});

describe('SubscriptionStrip rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders skeleton while loading', () => {
    const { container } = renderStrip({ data: undefined, isLoading: true, isError: false });
    expect(container.querySelector('.MuiSkeleton-root')).toBeTruthy();
  });

  it('renders nothing on error (silently hides)', () => {
    const { container } = renderStrip({ data: undefined, isLoading: false, isError: true });
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when current_period_unknown', () => {
    const { container } = renderStrip(okQuery({ current_period_unknown: true }));
    expect(container.firstChild).toBeNull();
  });

  it('renders healthy strip with chips and Manage button', () => {
    renderStrip(
      okQuery({
        subscription_status: 'active',
        quantity: 5,
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    expect(screen.getByTestId('subscription-strip-healthy')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('5 seats')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /manage subscription in stripe/i })).toBeInTheDocument();
  });

  it('omits the seat chip when quantity is missing', () => {
    renderStrip(
      okQuery({
        subscription_status: 'active',
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    expect(screen.queryByText(/seats/i)).not.toBeInTheDocument();
  });

  it('renders payment-failed Alert with the date interpolated', () => {
    // Use a non-UTC timezone with a date that's clearly within that zone's calendar day.
    // 18:00Z on Apr 25 is 14:00 EDT (Apr 25) — unambiguous in America/New_York.
    renderStrip(
      okQuery({
        subscription_status: 'active',
        payment_failed_at: '2026-04-25T18:00:00Z',
        current_period_end: '2026-05-01T18:00:00Z',
      }),
      'America/New_York'
    );
    const strip = screen.getByTestId('subscription-strip-unhealthy');
    expect(strip).toHaveTextContent('Payment failed on Apr 25, 2026');
  });

  it('renders will-not-renew warning Alert with period end interpolated', () => {
    renderStrip(
      okQuery({
        subscription_status: 'active',
        cancel_at_period_end: true,
        payment_failed_at: null,
        current_period_end: '2026-05-01T18:00:00Z',
      }),
      'America/New_York'
    );
    const strip = screen.getByTestId('subscription-strip-unhealthy');
    expect(strip).toHaveTextContent('Subscription will not renew. Service ends May 1, 2026');
  });

  it('renders Manage in Stripe button inside the unhealthy Alert action slot', () => {
    renderStrip(okQuery({ subscription_status: 'canceled', current_period_end: '2026-05-01T00:00:00Z' }));
    const button = screen.getByRole('button', { name: /manage subscription in stripe/i });
    expect(button).toBeInTheDocument();
    // The button should be inside the Alert (which has the unhealthy testid)
    expect(screen.getByTestId('subscription-strip-unhealthy')).toContainElement(button);
  });

  it('opens the portal URL in a new tab when the Manage button is clicked', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    billingAPI.getPortalUrl.mockResolvedValue({
      data: { data: { url: 'https://billing.stripe.com/p/session/abc' } },
    });

    renderStrip(
      okQuery({
        subscription_status: 'active',
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    fireEvent.click(screen.getByRole('button', { name: /manage subscription in stripe/i }));

    await waitFor(() =>
      expect(openSpy).toHaveBeenCalledWith('https://billing.stripe.com/p/session/abc', '_blank', 'noopener,noreferrer')
    );
    openSpy.mockRestore();
  });

  it('shows a snackbar error when the portal call fails', async () => {
    billingAPI.getPortalUrl.mockRejectedValue(new Error('500 boom'));

    renderStrip(
      okQuery({
        subscription_status: 'active',
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    fireEvent.click(screen.getByRole('button', { name: /manage subscription in stripe/i }));

    await waitFor(() => expect(screen.getByText(/could not open the billing portal/i)).toBeInTheDocument());
  });
});
