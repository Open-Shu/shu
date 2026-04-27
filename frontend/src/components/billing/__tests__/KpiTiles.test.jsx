/**
 * Tests for the KpiTiles component.
 */

import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';

import KpiTiles from '../KpiTiles';

const renderTiles = (usageQuery) => {
  const theme = createTheme();
  return render(
    <ThemeProvider theme={theme}>
      <KpiTiles usageQuery={usageQuery} />
    </ThemeProvider>
  );
};

const okQuery = (data) => ({ data, isLoading: false, isError: false });

describe('KpiTiles', () => {
  it('renders four skeleton placeholders while loading', () => {
    const { container } = renderTiles({ data: undefined, isLoading: true, isError: false });
    expect(container.querySelectorAll('.MuiSkeleton-root').length).toBe(4);
  });

  it('renders all four KPI labels', () => {
    renderTiles(
      okQuery({
        total_cost_usd: 45.32,
        total_input_tokens: 1_250_000,
        total_output_tokens: 850_000,
        by_model: [],
      })
    );
    expect(screen.getByText('Total Cost')).toBeInTheDocument();
    expect(screen.getByText('Input Tokens')).toBeInTheDocument();
    expect(screen.getByText('Output Tokens')).toBeInTheDocument();
    expect(screen.getByText('Requests')).toBeInTheDocument();
  });

  it('formats Total Cost as currency', () => {
    renderTiles(
      okQuery({
        total_cost_usd: 45.32,
        total_input_tokens: 0,
        total_output_tokens: 0,
        by_model: [],
      })
    );
    expect(screen.getByText('$45.32')).toBeInTheDocument();
  });

  it('renders sub-cent costs without rounding to $0.00', () => {
    renderTiles(
      okQuery({
        total_cost_usd: 0.0042,
        total_input_tokens: 0,
        total_output_tokens: 0,
        by_model: [],
      })
    );
    expect(screen.getByText('$0.0042')).toBeInTheDocument();
  });

  it('renders compact token values', () => {
    renderTiles(
      okQuery({
        total_cost_usd: 0,
        total_input_tokens: 1_250_000,
        total_output_tokens: 850_000,
        by_model: [],
      })
    );
    expect(screen.getByText('1.25M')).toBeInTheDocument();
    expect(screen.getByText('850K')).toBeInTheDocument();
  });

  it('compact-formatted token tiles carry an aria-label with the full count', () => {
    renderTiles(
      okQuery({
        total_cost_usd: 0,
        total_input_tokens: 1_250_000,
        total_output_tokens: 850_000,
        by_model: [],
      })
    );
    expect(screen.getByLabelText('Input tokens: 1,250,000')).toBeInTheDocument();
    expect(screen.getByLabelText('Output tokens: 850,000')).toBeInTheDocument();
  });

  it('Requests tile sums request_count across by_model rows', () => {
    renderTiles(
      okQuery({
        total_cost_usd: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        by_model: [
          { model_id: 'a', request_count: 324, cost_usd: 1, input_tokens: 0, output_tokens: 0 },
          { model_id: 'b', request_count: 156, cost_usd: 1, input_tokens: 0, output_tokens: 0 },
          { model_id: 'c', request_count: 92, cost_usd: 1, input_tokens: 0, output_tokens: 0 },
        ],
      })
    );
    expect(screen.getByText('572')).toBeInTheDocument();
  });

  it('Requests tile shows 0 when by_model is missing or empty', () => {
    renderTiles(
      okQuery({
        total_cost_usd: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
      })
    );
    // Input/Output token tiles also render "0"; assert via the requests aria-label.
    expect(screen.getByLabelText('Requests: 0')).toBeInTheDocument();
  });

  it('renders all em-dash placeholders when current_period_unknown is true', () => {
    renderTiles(okQuery({ current_period_unknown: true }));
    // All four tiles should render the placeholder; getAllByText finds all four.
    expect(screen.getAllByText('—').length).toBe(4);
  });

  it('zero-usage state renders explicit zeros, not placeholders', () => {
    renderTiles(
      okQuery({
        total_cost_usd: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        by_model: [],
      })
    );
    expect(screen.getByText('$0.00')).toBeInTheDocument();
    // 0 tokens and 0 requests both render as "0"; there should be multiple
    expect(screen.getAllByText('0').length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByText('—')).not.toBeInTheDocument();
  });
});
