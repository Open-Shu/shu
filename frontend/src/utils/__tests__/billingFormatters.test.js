/**
 * Unit tests for Cost & Usage dashboard formatters.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  formatCurrency,
  formatCompactTokens,
  formatFullTokens,
  formatBillingPeriod,
  formatLastUpdated,
  computeSharePercent,
} from '../billingFormatters';

describe('billingFormatters', () => {
  describe('formatCurrency', () => {
    it('renders a typical value with two decimals', () => {
      expect(formatCurrency(45.32)).toBe('$45.32');
    });

    it('renders sub-cent values with extra precision instead of $0.00', () => {
      expect(formatCurrency(0.0042)).toBe('$0.0042');
    });

    it('renders zero as $0.00', () => {
      expect(formatCurrency(0)).toBe('$0.00');
    });

    it('renders a large value with thousands separators', () => {
      expect(formatCurrency(123456.78)).toBe('$123,456.78');
    });

    it('preserves up to four fraction digits when present', () => {
      expect(formatCurrency(123456.789)).toBe('$123,456.789');
    });

    it('returns the placeholder for null', () => {
      expect(formatCurrency(null)).toBe('—');
    });

    it('returns the placeholder for undefined', () => {
      expect(formatCurrency(undefined)).toBe('—');
    });

    it('parses numeric strings (the API may return DECIMAL as a string)', () => {
      expect(formatCurrency('45.32')).toBe('$45.32');
    });

    it('returns the placeholder for non-numeric strings', () => {
      expect(formatCurrency('not-a-number')).toBe('—');
    });
  });

  describe('formatCompactTokens', () => {
    it('formats millions as M', () => {
      expect(formatCompactTokens(1_250_000)).toBe('1.25M');
    });

    it('formats thousands as K', () => {
      expect(formatCompactTokens(850_000)).toBe('850K');
    });

    it('keeps small counts un-abbreviated', () => {
      expect(formatCompactTokens(999)).toBe('999');
    });

    it('renders zero as 0', () => {
      expect(formatCompactTokens(0)).toBe('0');
    });

    it('returns the placeholder for null', () => {
      expect(formatCompactTokens(null)).toBe('—');
    });

    it('returns the placeholder for undefined', () => {
      expect(formatCompactTokens(undefined)).toBe('—');
    });
  });

  describe('formatFullTokens', () => {
    it('formats with thousands separators', () => {
      expect(formatFullTokens(1_250_000)).toBe('1,250,000');
    });

    it('returns the placeholder for null', () => {
      expect(formatFullTokens(null)).toBe('—');
    });
  });

  describe('formatBillingPeriod', () => {
    it('renders a same-year period with the year only on the end', () => {
      expect(formatBillingPeriod('2026-04-01T00:00:00Z', '2026-04-30T23:59:59Z', 'UTC')).toBe('Apr 1 – Apr 30, 2026');
    });

    it('renders a cross-year period with the year on both ends', () => {
      expect(formatBillingPeriod('2025-12-28T00:00:00Z', '2026-01-27T23:59:59Z', 'UTC')).toBe(
        'Dec 28, 2025 – Jan 27, 2026'
      );
    });

    it('renders in the supplied timezone', () => {
      // 2026-04-01T03:00:00Z is 2026-03-31 in America/New_York (EDT, -04:00)
      const result = formatBillingPeriod('2026-04-01T03:00:00Z', '2026-04-30T23:59:59Z', 'America/New_York');
      expect(result).toBe('Mar 31 – Apr 30, 2026');
    });

    it('returns the placeholder when start is missing', () => {
      expect(formatBillingPeriod(null, '2026-04-30T23:59:59Z', 'UTC')).toBe('—');
    });

    it('returns the placeholder when end is missing', () => {
      expect(formatBillingPeriod('2026-04-01T00:00:00Z', null, 'UTC')).toBe('—');
    });

    it('returns the placeholder when either input is invalid', () => {
      expect(formatBillingPeriod('garbage', '2026-04-30T23:59:59Z', 'UTC')).toBe('—');
    });
  });

  describe('formatLastUpdated', () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2026-04-27T12:00:00Z'));
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it('renders a relative time with " ago" suffix', () => {
      const twoMinAgo = new Date('2026-04-27T11:58:00Z');
      expect(formatLastUpdated(twoMinAgo)).toBe('2 minutes ago');
    });

    it('returns "never" for null', () => {
      expect(formatLastUpdated(null)).toBe('never');
    });

    it('returns "never" for undefined', () => {
      expect(formatLastUpdated(undefined)).toBe('never');
    });

    it('returns "never" for zero (React Query reports 0 before any fetch resolves)', () => {
      expect(formatLastUpdated(0)).toBe('never');
    });
  });

  describe('computeSharePercent', () => {
    it('returns the rounded percent for a typical case', () => {
      expect(computeSharePercent(22.5, 45.32)).toBe(50);
    });

    it('returns 0 when totalCost is zero (no divide-by-zero)', () => {
      expect(computeSharePercent(10, 0)).toBe(0);
    });

    it('returns 0 when totalCost is negative', () => {
      expect(computeSharePercent(10, -5)).toBe(0);
    });

    it('clamps to 100 when row exceeds total (defensive)', () => {
      expect(computeSharePercent(150, 100)).toBe(100);
    });

    it('returns 0 for non-finite inputs', () => {
      expect(computeSharePercent(NaN, 100)).toBe(0);
      expect(computeSharePercent(10, NaN)).toBe(0);
    });

    it('returns 0 when row cost is negative', () => {
      expect(computeSharePercent(-5, 100)).toBe(0);
    });
  });
});
