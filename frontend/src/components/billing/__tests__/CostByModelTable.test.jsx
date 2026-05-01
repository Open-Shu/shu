/**
 * Tests for the CostByModelTable component and its pure helpers.
 */

import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';

import CostByModelTable, { buildModelRow, orderRows } from '../CostByModelTable';

const renderTable = (usageQuery, modelsMap = new Map()) => {
  const theme = createTheme();
  return render(
    <ThemeProvider theme={theme}>
      <CostByModelTable usageQuery={usageQuery} modelsMap={modelsMap} />
    </ThemeProvider>
  );
};

const okQuery = (data) => ({ data, isLoading: false, isError: false });

describe('buildModelRow', () => {
  it('resolves display_name and provider_name from the models map', () => {
    const map = new Map([['m-1', { display_name: 'Claude Haiku 4.5', provider_name: 'anthropic' }]]);
    const raw = {
      model_id: 'm-1',
      cost_usd: 22.5,
      input_tokens: 500_000,
      output_tokens: 350_000,
      request_count: 324,
    };
    const row = buildModelRow(raw, map, 45);
    expect(row.displayName).toBe('Claude Haiku 4.5');
    expect(row.providerName).toBe('anthropic');
    expect(row.cost).toBe(22.5);
    expect(row.sharePercent).toBe(50);
    expect(row.isUnattributed).toBe(false);
  });

  it('falls back to the backend snapshot model_name when the catalog lookup misses', () => {
    // Models that have been deleted from llm_models still surface a readable
    // label via the per-row snapshot column populated at insert time (SHU-727).
    const raw = {
      model_id: 'a3f9b2d4-c1e5-4a8c-9f3e-2d6b8c4a1e7f',
      model_name: 'claude-haiku-4-5-20251001',
      cost_usd: 1,
    };
    const row = buildModelRow(raw, new Map(), 10);
    expect(row.displayName).toBe('claude-haiku-4-5-20251001');
    expect(row.providerName).toBeNull();
    expect(row.isUnattributed).toBe(false);
  });

  it('falls back to a truncated UUID only when neither the catalog nor the snapshot has a name', () => {
    const raw = { model_id: 'a3f9b2d4-c1e5-4a8c-9f3e-2d6b8c4a1e7f', cost_usd: 1 };
    const row = buildModelRow(raw, new Map(), 10);
    expect(row.displayName).toBe('model_a3f9b2d4');
    expect(row.providerName).toBeNull();
    expect(row.isUnattributed).toBe(false);
  });

  it('prefers the live catalog display_name over the snapshot model_name when both are present', () => {
    const map = new Map([['m-1', { display_name: 'Claude Haiku 4.5', provider_name: 'anthropic' }]]);
    const raw = { model_id: 'm-1', model_name: 'claude-haiku-4-5-20251001', cost_usd: 1 };
    const row = buildModelRow(raw, map, 10);
    expect(row.displayName).toBe('Claude Haiku 4.5');
    expect(row.providerName).toBe('anthropic');
  });

  it('treats null model_id as Unattributed', () => {
    const raw = { model_id: null, cost_usd: 0, request_count: 0 };
    const row = buildModelRow(raw, new Map(), 10);
    expect(row.displayName).toBe('Unattributed');
    expect(row.isUnattributed).toBe(true);
  });

  it('uses 0 share when totalCost is zero', () => {
    const row = buildModelRow({ model_id: 'm-1', cost_usd: 0 }, new Map(), 0);
    expect(row.sharePercent).toBe(0);
  });
});

describe('orderRows', () => {
  it('sorts named rows by cost desc', () => {
    const rows = [
      { key: 'a', cost: 5, isUnattributed: false },
      { key: 'b', cost: 22.5, isUnattributed: false },
      { key: 'c', cost: 12.3, isUnattributed: false },
    ];
    const ordered = orderRows(rows);
    expect(ordered.map((r) => r.key)).toEqual(['b', 'c', 'a']);
  });

  it('pins Unattributed to the bottom regardless of cost', () => {
    const rows = [
      { key: 'a', cost: 5, isUnattributed: false },
      { key: 'unattr', cost: 100, isUnattributed: true },
      { key: 'b', cost: 22.5, isUnattributed: false },
    ];
    const ordered = orderRows(rows);
    expect(ordered.map((r) => r.key)).toEqual(['b', 'a', 'unattr']);
  });
});

describe('CostByModelTable rendering', () => {
  it('renders skeleton placeholders while usage is loading', () => {
    const { container } = renderTable({ data: undefined, isLoading: true, isError: false }, new Map());
    // Header skeleton + 5 row skeletons = 6 total.
    expect(container.querySelectorAll('.MuiSkeleton-root').length).toBe(6);
  });

  it('renders rows with snapshot model_name even when the modelsMap is still empty', () => {
    // The table no longer waits on the models/providers fetches — rows
    // surface immediately using the backend snapshot, then upgrade to
    // catalog display_name when modelsMap arrives.
    renderTable(
      okQuery({
        total_cost_usd: 5,
        by_model: [
          {
            model_id: 'm-1',
            model_name: 'claude-haiku-4-5',
            cost_usd: 5,
            input_tokens: 0,
            output_tokens: 0,
            request_count: 0,
          },
        ],
      }),
      new Map() // catalog hasn't loaded yet
    );
    expect(screen.getByText('claude-haiku-4-5')).toBeInTheDocument();
    expect(screen.queryByText(/^model_/)).not.toBeInTheDocument();
  });

  it('shows the period-unknown placeholder copy', () => {
    renderTable(okQuery({ current_period_unknown: true }));
    expect(screen.getByText('Cost data will appear here once a billing period is active.')).toBeInTheDocument();
  });

  it('shows the zero-usage placeholder when by_model is empty for a valid period', () => {
    renderTable(okQuery({ total_cost_usd: 0, by_model: [] }));
    expect(screen.getByText('No LLM usage recorded in this billing period yet.')).toBeInTheDocument();
  });

  it('renders one row per by_model entry, sorted by cost desc', () => {
    const modelsMap = new Map([
      ['m-1', { display_name: 'Claude Haiku 4.5', provider_name: 'anthropic' }],
      ['m-2', { display_name: 'GPT-4o mini', provider_name: 'openai' }],
      ['m-3', { display_name: 'Sonnet 4.6', provider_name: 'anthropic' }],
    ]);
    renderTable(
      okQuery({
        total_cost_usd: 43.2,
        by_model: [
          { model_id: 'm-1', cost_usd: 22.5, input_tokens: 0, output_tokens: 0, request_count: 0 },
          { model_id: 'm-2', cost_usd: 12.3, input_tokens: 0, output_tokens: 0, request_count: 0 },
          { model_id: 'm-3', cost_usd: 8.4, input_tokens: 0, output_tokens: 0, request_count: 0 },
        ],
      }),
      modelsMap
    );

    const rows = screen.getAllByRole('row');
    // First row is the header; data rows follow.
    expect(within(rows[1]).getByText('Claude Haiku 4.5')).toBeInTheDocument();
    expect(within(rows[2]).getByText('GPT-4o mini')).toBeInTheDocument();
    expect(within(rows[3]).getByText('Sonnet 4.6')).toBeInTheDocument();
  });

  it('renders provider name as a subtitle under the model name', () => {
    const modelsMap = new Map([['m-1', { display_name: 'Claude Haiku 4.5', provider_name: 'anthropic' }]]);
    renderTable(
      okQuery({
        total_cost_usd: 5,
        by_model: [{ model_id: 'm-1', cost_usd: 5, input_tokens: 100, output_tokens: 50, request_count: 1 }],
      }),
      modelsMap
    );
    expect(screen.getByText('anthropic')).toBeInTheDocument();
  });

  it('renders Unattributed pinned to the bottom even when its cost is highest', () => {
    const modelsMap = new Map([['m-1', { display_name: 'Claude Haiku', provider_name: 'anthropic' }]]);
    renderTable(
      okQuery({
        total_cost_usd: 105,
        by_model: [
          { model_id: 'm-1', cost_usd: 5, request_count: 1 },
          { model_id: null, cost_usd: 100, request_count: 99 },
        ],
      }),
      modelsMap
    );
    const rows = screen.getAllByRole('row');
    // Header (0), data row 1 = Claude Haiku, data row 2 = Unattributed.
    expect(within(rows[1]).getByText('Claude Haiku')).toBeInTheDocument();
    expect(within(rows[2]).getByText('Unattributed')).toBeInTheDocument();
    expect(within(rows[2]).getByTestId('unattributed-info-icon')).toBeInTheDocument();
  });

  it('falls back to truncated UUID when modelsMap lookup misses', () => {
    renderTable(
      okQuery({
        total_cost_usd: 5,
        by_model: [
          {
            model_id: 'a3f9b2d4-c1e5-4a8c-9f3e-2d6b8c4a1e7f',
            cost_usd: 5,
            input_tokens: 0,
            output_tokens: 0,
            request_count: 0,
          },
        ],
      }),
      new Map()
    );
    expect(screen.getByText('model_a3f9b2d4')).toBeInTheDocument();
  });

  it('renders the share progress bar with the right aria-label', () => {
    const modelsMap = new Map([['m-1', { display_name: 'Claude Haiku', provider_name: 'anthropic' }]]);
    renderTable(
      okQuery({
        total_cost_usd: 100,
        by_model: [{ model_id: 'm-1', cost_usd: 49, input_tokens: 0, output_tokens: 0, request_count: 0 }],
      }),
      modelsMap
    );
    expect(screen.getByLabelText('49% of total cost')).toBeInTheDocument();
    expect(screen.getByText('49%')).toBeInTheDocument();
  });

  it('renders compact token cells with full-count tooltips and aria-labels', () => {
    const modelsMap = new Map([['m-1', { display_name: 'Claude Haiku', provider_name: 'anthropic' }]]);
    renderTable(
      okQuery({
        total_cost_usd: 5,
        by_model: [
          {
            model_id: 'm-1',
            cost_usd: 5,
            input_tokens: 1_250_000,
            output_tokens: 850_000,
            request_count: 0,
          },
        ],
      }),
      modelsMap
    );
    expect(screen.getByText('1.25M')).toBeInTheDocument();
    expect(screen.getByText('850K')).toBeInTheDocument();
    expect(screen.getByLabelText('Input tokens: 1,250,000')).toBeInTheDocument();
    expect(screen.getByLabelText('Output tokens: 850,000')).toBeInTheDocument();
  });
});
