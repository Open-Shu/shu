/**
 * Unit tests for the buildDailySeries transform (SHU-844).
 *
 * Pure pivot from the /billing/usage/me `by_day` payload into a sorted date
 * axis + one cost series per model. No React/MUI.
 */

import { describe, it, expect } from 'vitest';
import { buildDailySeries } from '../myUsageChart';

describe('buildDailySeries', () => {
  it('sums cost per (model, date) and keeps a series per model', () => {
    const byDay = [
      { date: '2026-04-01', model_id: 'm-1', model_name: 'Haiku', cost_usd: 10.5 },
      { date: '2026-04-01', model_id: 'm-1', model_name: 'Haiku', cost_usd: 5.5 },
      { date: '2026-04-01', model_id: 'm-2', model_name: 'GPT-4o', cost_usd: 3.0 },
    ];
    const { dates, series } = buildDailySeries(byDay);
    expect(dates).toEqual(['2026-04-01']);
    expect(series).toHaveLength(2);
    expect(series[0].modelId).toBe('m-1');
    expect(series[0].data).toEqual([16.0]);
    expect(series[1].modelId).toBe('m-2');
    expect(series[1].data).toEqual([3.0]);
  });

  it('sorts dates chronologically and aligns each series to them', () => {
    const byDay = [
      { date: '2026-04-03', model_id: 'm-1', cost_usd: 1 },
      { date: '2026-04-01', model_id: 'm-1', cost_usd: 2 },
      { date: '2026-04-02', model_id: 'm-1', cost_usd: 3 },
    ];
    const { dates, series } = buildDailySeries(byDay);
    expect(dates).toEqual(['2026-04-01', '2026-04-02', '2026-04-03']);
    expect(series[0].data).toEqual([2, 3, 1]);
  });

  it('zero-fills a model on dates it lacks but another model has', () => {
    // The axis is the union of dates present in the data (absent calendar days
    // are not synthesized); each series is zero-filled to align to that axis.
    const byDay = [
      { date: '2026-04-01', model_id: 'm-1', cost_usd: 10 },
      { date: '2026-04-02', model_id: 'm-2', cost_usd: 20 },
    ];
    const { dates, series } = buildDailySeries(byDay);
    expect(dates).toEqual(['2026-04-01', '2026-04-02']);
    expect(series[0].data).toEqual([10, 0]);
    expect(series[1].data).toEqual([0, 20]);
  });

  it('preserves first-seen model order', () => {
    const byDay = [
      { date: '2026-04-01', model_id: 'm-3', cost_usd: 1 },
      { date: '2026-04-01', model_id: 'm-1', cost_usd: 2 },
      { date: '2026-04-01', model_id: 'm-2', cost_usd: 3 },
    ];
    const { series } = buildDailySeries(byDay);
    expect(series.map((s) => s.modelId)).toEqual(['m-3', 'm-1', 'm-2']);
  });

  it('returns empty for empty or null input', () => {
    expect(buildDailySeries([])).toEqual({ dates: [], series: [] });
    expect(buildDailySeries(null)).toEqual({ dates: [], series: [] });
    expect(buildDailySeries(undefined)).toEqual({ dates: [], series: [] });
  });

  it('skips rows missing a date', () => {
    const byDay = [
      { model_id: 'm-1', cost_usd: 10 },
      { date: '2026-04-01', model_id: 'm-1', cost_usd: 5 },
    ];
    const { dates, series } = buildDailySeries(byDay);
    expect(dates).toEqual(['2026-04-01']);
    expect(series[0].data).toEqual([5]);
  });

  describe('label resolution', () => {
    it('prefers the live catalog display_name', () => {
      const modelsMap = new Map([['m-1', { display_name: 'Claude Haiku 4.5', provider_name: 'anthropic' }]]);
      const byDay = [{ date: '2026-04-01', model_id: 'm-1', model_name: 'claude-haiku', cost_usd: 10 }];
      expect(buildDailySeries(byDay, modelsMap).series[0].label).toBe('Claude Haiku 4.5');
    });

    it('falls back to the snapshot model_name', () => {
      const byDay = [{ date: '2026-04-01', model_id: 'm-1', model_name: 'claude-haiku-4-5', cost_usd: 10 }];
      expect(buildDailySeries(byDay, new Map()).series[0].label).toBe('claude-haiku-4-5');
    });

    it('falls back to a truncated id when nothing resolves', () => {
      const byDay = [{ date: '2026-04-01', model_id: 'a3f9b2d4-c1e5-4a8c-9f3e-2d6b8c4a1e7f', cost_usd: 10 }];
      expect(buildDailySeries(byDay, new Map()).series[0].label).toBe('model_a3f9b2d4');
    });

    it('merges null and "unknown" model ids into one "Unattributed" series', () => {
      const byDay = [
        { date: '2026-04-01', model_id: null, cost_usd: 5 },
        { date: '2026-04-01', model_id: 'unknown', cost_usd: 3 },
      ];
      const { series } = buildDailySeries(byDay);
      expect(series).toHaveLength(1);
      expect(series[0].label).toBe('Unattributed');
      expect(series[0].data).toEqual([8]);
    });
  });

  describe('numeric coercion', () => {
    it('coerces string costs to numbers', () => {
      const byDay = [{ date: '2026-04-01', model_id: 'm-1', cost_usd: '12.5' }];
      expect(buildDailySeries(byDay).series[0].data[0]).toBe(12.5);
    });

    it('treats missing or non-numeric cost as 0', () => {
      const byDay = [
        { date: '2026-04-01', model_id: 'm-1', cost_usd: undefined },
        { date: '2026-04-01', model_id: 'm-2', cost_usd: 'nope' },
      ];
      const { series } = buildDailySeries(byDay);
      expect(series[0].data[0]).toBe(0);
      expect(series[1].data[0]).toBe(0);
    });
  });
});
