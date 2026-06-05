/**
 * Tests for MyUsageKpiTiles (SHU-844).
 *
 * Three volume tiles from per-user usage, plus a fourth Shared-Pool tile when a
 * positive pool is supplied. All data is via props (no hooks/context).
 */

import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';

import MyUsageKpiTiles from '../MyUsageKpiTiles';

const renderTiles = (usageData, isLoading = false, pool = null) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <MyUsageKpiTiles usageData={usageData} isLoading={isLoading} pool={pool} />
    </ThemeProvider>
  );

const usage = (overrides = {}) => ({
  current_period_unknown: false,
  total_cost_usd: 50,
  request_count: 1000,
  total_input_tokens: 500_000,
  total_output_tokens: 350_000,
  ...overrides,
});

describe('MyUsageKpiTiles', () => {
  describe('without a pool', () => {
    it('renders exactly 3 volume tiles', () => {
      const { container } = renderTiles(usage(), false, null);
      expect(container.querySelectorAll('.MuiCard-root')).toHaveLength(3);
      expect(screen.getByText('Your Usage Cost')).toBeInTheDocument();
      expect(screen.getByText('Requests')).toBeInTheDocument();
      expect(screen.getByText('Tokens')).toBeInTheDocument();
      expect(screen.queryByText('Shared Pool')).not.toBeInTheDocument();
    });

    it('formats cost as currency', () => {
      renderTiles(usage({ total_cost_usd: 12.34 }), false, null);
      expect(screen.getByText('$12.34')).toBeInTheDocument();
    });

    it('formats requests with thousands separators', () => {
      renderTiles(usage({ request_count: 1_234_567 }), false, null);
      expect(screen.getByText('1,234,567')).toBeInTheDocument();
    });

    it('formats tokens compactly with the full count in an aria-label', () => {
      renderTiles(usage({ total_input_tokens: 1_250_000, total_output_tokens: 850_000 }), false, null);
      expect(screen.getByText('2.1M')).toBeInTheDocument();
      expect(screen.getByLabelText(/Tokens: 2,100,000/)).toBeInTheDocument();
    });
  });

  describe('with a positive pool', () => {
    it('renders a 4th Shared Pool tile with used / total', () => {
      const { container } = renderTiles(usage(), false, { total: 500, remaining: 100 });
      expect(container.querySelectorAll('.MuiCard-root')).toHaveLength(4);
      expect(screen.getByText('Shared Pool')).toBeInTheDocument();
      expect(screen.getByText('$400.00 / $500.00')).toBeInTheDocument();
      expect(screen.getByText(/across all seats & shared activity/)).toBeInTheDocument();
      expect(screen.getByRole('progressbar')).toBeInTheDocument();
    });

    it('omits the pool tile when total is zero', () => {
      const { container } = renderTiles(usage(), false, { total: 0, remaining: 0 });
      expect(container.querySelectorAll('.MuiCard-root')).toHaveLength(3);
    });

    it('clamps remaining above total so used never goes negative', () => {
      // remaining > total → clamped to total → used 0 → "$0.00 / $100.00"
      renderTiles(usage(), false, { total: 100, remaining: 150 });
      expect(screen.getByText('$0.00 / $100.00')).toBeInTheDocument();
    });
  });

  describe('period unknown', () => {
    it('shows placeholders for the volume tiles', () => {
      renderTiles(usage({ current_period_unknown: true }), false, null);
      expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3);
    });
  });

  describe('loading', () => {
    it('renders 3 skeletons without a pool', () => {
      const { container } = renderTiles(usage(), true, null);
      expect(container.querySelectorAll('.MuiSkeleton-root').length).toBeGreaterThanOrEqual(3);
    });

    it('renders 4 skeletons with a pool', () => {
      const { container } = renderTiles(usage(), true, { total: 500, remaining: 100 });
      expect(container.querySelectorAll('.MuiSkeleton-root').length).toBeGreaterThanOrEqual(4);
    });
  });
});
