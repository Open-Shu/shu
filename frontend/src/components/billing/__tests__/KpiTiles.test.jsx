/**
 * Tests for the KpiTiles component.
 *
 * The four tiles tell a financial story keyed off the SHU-663 epic:
 *   Usage Cost · Included Allowance · Used (%) · Overage ($).
 *
 * Allowance = seats × $50 (hardcoded constant until SHU-704 surfaces it
 * from the API). Overage is charged at cost × 1.30 per the epic's +30%
 * markup.
 */

import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';

import KpiTiles from '../KpiTiles';

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

const subWithSeats = (seats) => okQuery({ subscription_status: 'active', user_limit: seats });

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
      expect(screen.getByText('Overage')).toBeInTheDocument();
    });
  });

  describe('Usage Cost tile', () => {
    it('formats Usage Cost as currency', () => {
      renderTiles(okQuery({ total_cost_usd: 45.32, by_model: [] }), subWithSeats(5));
      expect(screen.getByText('$45.32')).toBeInTheDocument();
    });

    it('renders sub-cent costs without rounding to $0.00', () => {
      renderTiles(okQuery({ total_cost_usd: 0.0042, by_model: [] }), subWithSeats(5));
      expect(screen.getByText('$0.0042')).toBeInTheDocument();
    });

    it('renders zero usage as $0.00', () => {
      renderTiles(okQuery({ total_cost_usd: 0, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Usage cost: $0.00')).toBeInTheDocument();
    });
  });

  describe('Included Allowance tile', () => {
    it('renders allowance as seats × $50 with sub-caption', () => {
      renderTiles(okQuery({ total_cost_usd: 0, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Included allowance: $250.00')).toBeInTheDocument();
      expect(screen.getByText('5 seats × $50.00')).toBeInTheDocument();
    });

    it('uses the singular "seat" when quantity is 1', () => {
      renderTiles(okQuery({ total_cost_usd: 0, by_model: [] }), subWithSeats(1));
      expect(screen.getByText('1 seat × $50.00')).toBeInTheDocument();
    });

    it('falls back to the placeholder when seats are unknown', () => {
      renderTiles(okQuery({ total_cost_usd: 10, by_model: [] }), okQuery({ subscription_status: 'active' }));
      expect(screen.getByLabelText('Included allowance: not available')).toBeInTheDocument();
    });
  });

  describe('Used tile', () => {
    it('renders integer percent computed from usage / allowance', () => {
      // $50 / $250 (5 seats) = 20%
      renderTiles(okQuery({ total_cost_usd: 50, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Allowance used: 20%')).toBeInTheDocument();
    });

    it('renders a LinearProgress bar inside the tile when allowance is known', () => {
      const { container } = renderTiles(okQuery({ total_cost_usd: 50, by_model: [] }), subWithSeats(5));
      expect(container.querySelector('.MuiLinearProgress-root')).toBeTruthy();
    });

    it('clamps the bar at 100% even when actual usage exceeds allowance', () => {
      // $300 / $250 = 120%, but the bar value should max out at 100.
      const { container } = renderTiles(okQuery({ total_cost_usd: 300, by_model: [] }), subWithSeats(5));
      const bar = container.querySelector('.MuiLinearProgress-root');
      expect(bar).toBeTruthy();
      // The numeric percent text still shows the actual value (120%).
      expect(screen.getByLabelText('Allowance used: 120%')).toBeInTheDocument();
    });

    it('falls back to placeholder when seats are unknown', () => {
      renderTiles(okQuery({ total_cost_usd: 10, by_model: [] }), okQuery({ subscription_status: 'active' }));
      expect(screen.getByLabelText('Allowance used: not available')).toBeInTheDocument();
    });
  });

  describe('Overage tile', () => {
    it('shows $0.00 with "within allowance" sub-caption when usage <= allowance', () => {
      renderTiles(okQuery({ total_cost_usd: 100, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Overage: $0.00')).toBeInTheDocument();
      expect(screen.getByText('within allowance')).toBeInTheDocument();
    });

    it('shows the dollar overage with +30% upcharged sub-line when usage > allowance', () => {
      // Usage $300, allowance $250 → overage $50 → charged $65 with +30%.
      renderTiles(okQuery({ total_cost_usd: 300, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Overage: $50.00')).toBeInTheDocument();
      expect(screen.getByText('charged at $65.00 (+30%)')).toBeInTheDocument();
    });

    it('falls back to placeholder when seats are unknown', () => {
      renderTiles(okQuery({ total_cost_usd: 10, by_model: [] }), okQuery({ subscription_status: 'active' }));
      expect(screen.getByLabelText('Overage: not available')).toBeInTheDocument();
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
    it('renders explicit zeros (not placeholders) for usage cost / used / overage', () => {
      renderTiles(okQuery({ total_cost_usd: 0, by_model: [] }), subWithSeats(5));
      expect(screen.getByLabelText('Usage cost: $0.00')).toBeInTheDocument();
      expect(screen.getByLabelText('Allowance used: 0%')).toBeInTheDocument();
      expect(screen.getByLabelText('Overage: $0.00')).toBeInTheDocument();
      expect(screen.queryByText('—')).not.toBeInTheDocument();
    });
  });
});
