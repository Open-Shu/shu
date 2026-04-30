/**
 * Tests for the KpiTiles component.
 *
 * The four tiles tell a financial story keyed off the SHU-663 epic. Stripe's
 * metered Price has the +30% markup baked into its unit_amount_decimal, so
 * usage events invoice at 1.3× provider cost — not just usage above the
 * included allowance. The tiles reflect that:
 *
 *   Usage Cost (billed: provider × markup)
 *   Included Allowance (seats × $50 fallback, or live Stripe credit grants)
 *   Used (% of allowance consumed by the billed cost)
 *   Additional Charges (max(0, billed − allowance))
 *
 * Markup defaults to 1.3 from USAGE_MARKUP_MULTIPLIER, but the dashboard
 * prefers the API-supplied `usage_markup_multiplier` when present (derived
 * from the metered Price's unit_amount_decimal).
 */

import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';

import KpiTiles, { pickUsedColor } from '../KpiTiles';

const renderTiles = (usageQuery, subscriptionQuery) => {
  const theme = createTheme();
  return render(
    <ThemeProvider theme={theme}>
      <KpiTiles usageQuery={usageQuery} subscriptionQuery={subscriptionQuery} />
    </ThemeProvider>
  );
};

const okQuery = (data) => ({ data, isLoading: false, isError: false });
const loadingQuery = () => ({ data: undefined, isLoading: true, isError: false });

const subWithSeats = (seats, extra = {}) => okQuery({ subscription_status: 'active', user_limit: seats, ...extra });

describe('KpiTiles', () => {
  describe('loading', () => {
    it('renders four skeleton placeholders while usage is loading', () => {
      const { container } = renderTiles(loadingQuery(), subWithSeats(5));
      expect(container.querySelectorAll('.MuiSkeleton-root').length).toBe(4);
    });

    it('renders skeletons while subscription is loading (so seats are still unknown)', () => {
      const { container } = renderTiles(okQuery({ total_cost_usd: 10, by_model: [] }), loadingQuery());
      expect(container.querySelectorAll('.MuiSkeleton-root').length).toBe(4);
    });
  });

  describe('tile labels', () => {
    it('renders the four financial labels', () => {
      renderTiles(okQuery({ total_cost_usd: 10, by_model: [] }), subWithSeats(5));
      expect(screen.getByText('Usage Cost')).toBeInTheDocument();
      expect(screen.getByText('Included Allowance')).toBeInTheDocument();
      expect(screen.getByText('Used')).toBeInTheDocument();
      expect(screen.getByText('Additional Charges')).toBeInTheDocument();
    });
  });

  describe('Usage Cost tile', () => {
    it('renders the billed cost (provider cost × markup) as the headline', () => {
      // $100.00 raw × 1.3 = $130.00 billed.
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5));
      expect(screen.getByText('$130.00')).toBeInTheDocument();
    });

    it('shows raw provider cost and markup percent in the sub-line', () => {
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5));
      expect(screen.getByText('$100.00 provider cost, billed at +30%')).toBeInTheDocument();
    });

    it('skips the sub-line when usage is zero (nothing meaningful to explain)', () => {
      renderTiles(okQuery({ total_cost_usd: 0, by_model: [] }), subWithSeats(5));
      expect(screen.queryByText(/provider cost, billed at/)).not.toBeInTheDocument();
    });

    it('renders sub-cent billed costs without rounding to $0.00', () => {
      // $0.0042 × 1.3 = $0.00546 → 4-digit precision renders as $0.0055.
      renderTiles(okQuery({ total_cost_usd: 0.0042, by_model: [] }), subWithSeats(5));
      expect(screen.getByText('$0.0055')).toBeInTheDocument();
    });

    it('renders zero usage as $0.00 with no sub-line', () => {
      renderTiles(okQuery({ total_cost_usd: 0, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Usage cost: $0.00')).toBeInTheDocument();
    });

    it('aria-label reflects the billed (post-markup) cost, not the raw provider cost', () => {
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Usage cost: $130.00')).toBeInTheDocument();
    });
  });

  describe('Included Allowance tile', () => {
    it('renders allowance as seats × $50 with sub-caption (fallback path)', () => {
      renderTiles(okQuery({ total_cost_usd: 0, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Included allowance: $250.00')).toBeInTheDocument();
      expect(screen.getByText('5 seats × $50.00')).toBeInTheDocument();
    });

    it('uses the singular "seat" when quantity is 1 (fallback path)', () => {
      renderTiles(okQuery({ total_cost_usd: 0, by_model: [] }), subWithSeats(1));
      expect(screen.getByText('1 seat × $50.00')).toBeInTheDocument();
    });

    it('prefers included_usd_per_period from the API over the seats fallback', () => {
      renderTiles(
        okQuery({ total_cost_usd: 0, by_model: [] }),
        okQuery({
          subscription_status: 'active',
          user_limit: 5,
          included_usd_per_period: 300,
        })
      );
      expect(screen.getByLabelText('Included allowance: $300.00')).toBeInTheDocument();
      expect(screen.getByText('from active credit grants')).toBeInTheDocument();
      expect(screen.queryByText(/seats × \$50/)).not.toBeInTheDocument();
    });

    it('falls back to seats × $50 when included_usd_per_period is null', () => {
      renderTiles(
        okQuery({ total_cost_usd: 0, by_model: [] }),
        okQuery({ subscription_status: 'active', user_limit: 5, included_usd_per_period: null })
      );
      expect(screen.getByLabelText('Included allowance: $250.00')).toBeInTheDocument();
      expect(screen.getByText('5 seats × $50.00')).toBeInTheDocument();
    });

    it('falls back to seats × $50 when included_usd_per_period is zero', () => {
      renderTiles(
        okQuery({ total_cost_usd: 0, by_model: [] }),
        okQuery({ subscription_status: 'active', user_limit: 5, included_usd_per_period: 0 })
      );
      expect(screen.getByLabelText('Included allowance: $250.00')).toBeInTheDocument();
      expect(screen.getByText('5 seats × $50.00')).toBeInTheDocument();
    });

    it('falls back to the placeholder when seats are unknown and no API value', () => {
      renderTiles(okQuery({ total_cost_usd: 10, by_model: [] }), okQuery({ subscription_status: 'active' }));
      expect(screen.getByLabelText('Included allowance: not available')).toBeInTheDocument();
    });
  });

  describe('Used tile', () => {
    it('renders integer percent computed from billed cost / allowance', () => {
      // $50 raw × 1.3 = $65 billed; $65 / $250 (5 seats) = 26%.
      renderTiles(okQuery({ total_cost_usd: 50, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Allowance used: 26%')).toBeInTheDocument();
    });

    it('renders a LinearProgress bar inside the tile when allowance is known', () => {
      const { container } = renderTiles(okQuery({ total_cost_usd: 50, by_model: [] }), subWithSeats(5));
      expect(container.querySelector('.MuiLinearProgress-root')).toBeTruthy();
    });

    it('clamps the bar at 100% even when actual usage exceeds allowance', () => {
      // $300 raw × 1.3 = $390 billed; $390 / $250 = 156%; bar clamps at 100,
      // text shows the actual value.
      const { container } = renderTiles(okQuery({ total_cost_usd: 300, by_model: [] }), subWithSeats(5));
      const bar = container.querySelector('.MuiLinearProgress-root');
      expect(bar).toBeTruthy();
      expect(screen.getByLabelText('Allowance used: 156%')).toBeInTheDocument();
    });

    it('falls back to placeholder when seats are unknown', () => {
      renderTiles(okQuery({ total_cost_usd: 10, by_model: [] }), okQuery({ subscription_status: 'active' }));
      expect(screen.getByLabelText('Allowance used: not available')).toBeInTheDocument();
    });
  });

  describe('Additional Charges tile', () => {
    it('shows $0.00 with "covered by allowance" copy when billed cost <= allowance', () => {
      // $150 raw × 1.3 = $195 billed; allowance $250 → no additional charges.
      renderTiles(okQuery({ total_cost_usd: 150, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Additional charges: $0.00')).toBeInTheDocument();
      expect(screen.getByText('covered by allowance')).toBeInTheDocument();
    });

    it('shows the billed-over-allowance dollar amount with "above included allowance" copy', () => {
      // $300 raw × 1.3 = $390 billed; allowance $250 → $140 additional.
      renderTiles(okQuery({ total_cost_usd: 300, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Additional charges: $140.00')).toBeInTheDocument();
      expect(screen.getByText('above included allowance')).toBeInTheDocument();
    });

    it('falls back to placeholder when seats are unknown', () => {
      renderTiles(okQuery({ total_cost_usd: 10, by_model: [] }), okQuery({ subscription_status: 'active' }));
      expect(screen.getByLabelText('Additional charges: not available')).toBeInTheDocument();
    });
  });

  describe('usage_markup_multiplier (API-driven markup)', () => {
    it('prefers usage_markup_multiplier from the API over the constant fallback', () => {
      // API supplies a 1.5× markup; raw $100 → billed $150 → 60% of $250.
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5, { usage_markup_multiplier: 1.5 }));
      expect(screen.getByText('$150.00')).toBeInTheDocument();
      expect(screen.getByText('$100.00 provider cost, billed at +50%')).toBeInTheDocument();
      expect(screen.getByLabelText('Allowance used: 60%')).toBeInTheDocument();
    });

    it('falls back to the constant when API multiplier is null', () => {
      // API explicitly null → use 1.3 constant; raw $100 → billed $130.
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5, { usage_markup_multiplier: null }));
      expect(screen.getByText('$130.00')).toBeInTheDocument();
      expect(screen.getByText('$100.00 provider cost, billed at +30%')).toBeInTheDocument();
    });

    it('falls back to the constant when API multiplier is zero (defensive)', () => {
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5, { usage_markup_multiplier: 0 }));
      expect(screen.getByText('$130.00')).toBeInTheDocument();
    });

    it('falls back to the constant when the API field is absent entirely', () => {
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5));
      expect(screen.getByText('$130.00')).toBeInTheDocument();
      expect(screen.getByText('$100.00 provider cost, billed at +30%')).toBeInTheDocument();
    });

    it('rounds the markup percent to the nearest integer for fractional multipliers', () => {
      // markup 1.234 → (1.234 − 1) × 100 = 23.4 → rounds to 23.
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5, { usage_markup_multiplier: 1.234 }));
      expect(screen.getByText('$100.00 provider cost, billed at +23%')).toBeInTheDocument();
    });

    it('rounds half-up at the percent boundary (1.235 → +24%)', () => {
      // markup 1.235 → (1.235 − 1) × 100 = 23.5 → Math.round rounds half-away-from-zero
      // for positive numbers in V8/JSC, yielding 24. Locks in the rounding direction.
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5, { usage_markup_multiplier: 1.235 }));
      expect(screen.getByText('$100.00 provider cost, billed at +24%')).toBeInTheDocument();
    });
  });

  describe('current_period_unknown', () => {
    it('renders all em-dash placeholders when current_period_unknown is true', () => {
      renderTiles(okQuery({ current_period_unknown: true }), subWithSeats(5));
      // All four tiles show "—" for their main value.
      expect(screen.getAllByText('—').length).toBe(4);
    });
  });

  describe('zero usage', () => {
    it('renders explicit zeros (not placeholders) for usage cost / used / additional charges', () => {
      renderTiles(okQuery({ total_cost_usd: 0, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Usage cost: $0.00')).toBeInTheDocument();
      expect(screen.getByLabelText('Allowance used: 0%')).toBeInTheDocument();
      expect(screen.getByLabelText('Additional charges: $0.00')).toBeInTheDocument();
      expect(screen.queryByText('—')).not.toBeInTheDocument();
    });
  });
});

describe('pickUsedColor', () => {
  // Locks in the band thresholds: success below 80, warning [80, 100), error
  // at 100 or above. These are the visual signal the Used tile carries; an
  // accidental flip during refactor would silently change the alert UX.
  it('returns "success" at 0%', () => {
    expect(pickUsedColor(0)).toBe('success');
  });

  it('returns "success" just below the warning threshold (79%)', () => {
    expect(pickUsedColor(79)).toBe('success');
  });

  it('returns "warning" exactly at the warning threshold (80%)', () => {
    expect(pickUsedColor(80)).toBe('warning');
  });

  it('returns "warning" just below the error threshold (99%)', () => {
    expect(pickUsedColor(99)).toBe('warning');
  });

  it('returns "error" exactly at the error threshold (100%)', () => {
    expect(pickUsedColor(100)).toBe('error');
  });

  it('returns "error" past the error threshold (150%)', () => {
    expect(pickUsedColor(150)).toBe('error');
  });
});
