/**
 * Tests for the SubscriptionStrip component and its pure view selector.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import SubscriptionStrip, { selectSubscriptionView } from '../SubscriptionStrip';

const renderStrip = (subscriptionQuery, timezone = 'UTC') => {
  const theme = createTheme();
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/admin/billing/usage']}>
        <Routes>
          <Route
            path="/admin/billing/usage"
            element={<SubscriptionStrip subscriptionQuery={subscriptionQuery} timezone={timezone} />}
          />
          <Route path="/admin/users" element={<div data-testid="user-management-landing">user-mgmt</div>} />
        </Routes>
      </MemoryRouter>
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

  it('renders healthy strip with chips and Manage button using user_limit (the API field)', () => {
    renderStrip(
      okQuery({
        subscription_status: 'active',
        user_limit: 5,
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    expect(screen.getByTestId('subscription-strip-healthy')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('5 seats')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /manage seats/i })).toBeInTheDocument();
  });

  it('also accepts quantity as a fallback (matches the underlying DB column name)', () => {
    renderStrip(
      okQuery({
        subscription_status: 'active',
        quantity: 7,
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    expect(screen.getByText('7 seats')).toBeInTheDocument();
  });

  it('uses the singular "seat" in the chip when there is exactly one seat', () => {
    renderStrip(
      okQuery({
        subscription_status: 'active',
        user_limit: 1,
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    expect(screen.getByText('1 seat')).toBeInTheDocument();
    expect(screen.queryByText('1 seats')).not.toBeInTheDocument();
  });

  it('omits the seat chip when neither user_limit nor quantity is present', () => {
    renderStrip(
      okQuery({
        subscription_status: 'active',
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    // Match chip pattern "<N> seat(s)" specifically, not the "Manage Seats" button.
    expect(screen.queryByText(/^\d+ seats?$/i)).not.toBeInTheDocument();
  });

  it('omits the seat chip when user_limit is zero (no real subscription quantity)', () => {
    renderStrip(
      okQuery({
        subscription_status: 'active',
        user_limit: 0,
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    // Match chip pattern "<N> seat(s)" specifically, not the "Manage Seats" button.
    expect(screen.queryByText(/^\d+ seats?$/i)).not.toBeInTheDocument();
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

  it('does not render the Manage Seats button in unhealthy modes', () => {
    // Unhealthy variants drop the action button entirely — Manage Seats doesn't
    // help with payment failures / cancellation, and the recovery surface for
    // those states is still TBD.
    renderStrip(okQuery({ subscription_status: 'canceled', current_period_end: '2026-05-01T00:00:00Z' }));
    expect(screen.getByTestId('subscription-strip-unhealthy')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /manage seats/i })).not.toBeInTheDocument();
  });

  it('navigates to /admin/users when the Manage Seats button is clicked (healthy mode)', () => {
    renderStrip(
      okQuery({
        subscription_status: 'active',
        cancel_at_period_end: false,
        payment_failed_at: null,
      })
    );
    fireEvent.click(screen.getByRole('button', { name: /manage seats/i }));
    expect(screen.getByTestId('user-management-landing')).toBeInTheDocument();
  });
});
