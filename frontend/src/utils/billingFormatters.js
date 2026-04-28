/**
 * Formatter helpers for the Cost & Usage dashboard.
 *
 * All helpers are pure and tolerant of nullish inputs so callers can pass
 * raw API response fields without pre-checking. The em-dash placeholder
 * `"—"` is returned for nullish values; numeric zero is preserved as a
 * meaningful value.
 */

import { format as dateFnsFormat, formatDistanceToNow } from 'date-fns';
import { formatInTimeZone } from 'date-fns-tz';
import log from './log';

const PLACEHOLDER = '—';

/**
 * USD per seat included in the monthly subscription, per the SHU-663 epic.
 * Today this is hardcoded; SHU-704 will surface the actual Stripe Credit
 * Grant size on the /billing/subscription response, at which point this
 * fallback should defer to the API value.
 */
export const INCLUDED_USAGE_PER_SEAT_USD = 50;

/**
 * Markup multiplier applied to overage above the included allowance, per
 * the SHU-663 epic ("Overage billed at actual provider cost + 30%").
 */
export const OVERAGE_MARKUP_MULTIPLIER = 1.3;

const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

const compactNumberFormatter = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 2,
});

const fullNumberFormatter = new Intl.NumberFormat('en-US');

/**
 * Format a USD cost. Renders 2-4 fraction digits so sub-cent values like
 * `$0.0042` are not misleadingly rendered as `$0.00`.
 *
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
export function formatCurrency(value) {
  if (value === null || value === undefined) {
    return PLACEHOLDER;
  }
  const num = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(num)) {
    return PLACEHOLDER;
  }
  return currencyFormatter.format(num);
}

/**
 * Format a token count in compact notation (e.g. 1.25M, 850K).
 *
 * @param {number|null|undefined} value
 * @returns {string}
 */
export function formatCompactTokens(value) {
  if (value === null || value === undefined) {
    return PLACEHOLDER;
  }
  if (!Number.isFinite(value)) {
    return PLACEHOLDER;
  }
  return compactNumberFormatter.format(value);
}

/**
 * Format a number with thousands separators. Used for tooltips on compact
 * values and for request counts.
 *
 * @param {number|null|undefined} value
 * @returns {string}
 */
export function formatFullTokens(value) {
  if (value === null || value === undefined) {
    return PLACEHOLDER;
  }
  if (!Number.isFinite(value)) {
    return PLACEHOLDER;
  }
  return fullNumberFormatter.format(value);
}

/**
 * Format a billing period as "Apr 1 – Apr 30, 2026" or, when the period
 * spans years, "Dec 28, 2025 – Jan 27, 2026".
 *
 * @param {Date|string|null|undefined} start
 * @param {Date|string|null|undefined} end
 * @param {string} [timezone] IANA timezone identifier; defaults to local time
 * @returns {string}
 */
export function formatBillingPeriod(start, end, timezone) {
  if (!start || !end) {
    return PLACEHOLDER;
  }

  try {
    const startDate = typeof start === 'string' ? new Date(start) : start;
    const endDate = typeof end === 'string' ? new Date(end) : end;

    if (isNaN(startDate.getTime()) || isNaN(endDate.getTime())) {
      return PLACEHOLDER;
    }

    const useTz = Boolean(timezone);
    const yearOf = (d) => (useTz ? Number(formatInTimeZone(d, timezone, 'yyyy')) : d.getFullYear());
    const fmt = (d, pattern) => (useTz ? formatInTimeZone(d, timezone, pattern) : dateFnsFormat(d, pattern));

    const sameYear = yearOf(startDate) === yearOf(endDate);

    if (sameYear) {
      return `${fmt(startDate, 'MMM d')} – ${fmt(endDate, 'MMM d, yyyy')}`;
    }
    return `${fmt(startDate, 'MMM d, yyyy')} – ${fmt(endDate, 'MMM d, yyyy')}`;
  } catch (error) {
    log.error('Error formatting billing period:', error);
    return PLACEHOLDER;
  }
}

/**
 * Format a "last updated" relative timestamp (e.g. "2 minutes ago").
 * Returns "never" for nullish input.
 *
 * @param {number|Date|null|undefined} timestamp
 * @returns {string}
 */
export function formatLastUpdated(timestamp) {
  if (timestamp === null || timestamp === undefined || timestamp === 0) {
    return 'never';
  }
  try {
    const date = typeof timestamp === 'number' ? new Date(timestamp) : timestamp;
    if (isNaN(date.getTime())) {
      return 'never';
    }
    return `${formatDistanceToNow(date)} ago`;
  } catch (error) {
    log.error('Error formatting last-updated timestamp:', error);
    return 'never';
  }
}

/**
 * Compute a row's share of a total as a rounded integer percent.
 * Defends against divide-by-zero (`totalCost === 0`) and clamps over-100
 * values that should not occur but would render badly in a progress bar.
 *
 * @param {number} rowCost
 * @param {number} totalCost
 * @returns {number} integer 0..100
 */
export function computeSharePercent(rowCost, totalCost) {
  if (!Number.isFinite(rowCost) || !Number.isFinite(totalCost) || totalCost <= 0) {
    return 0;
  }
  const raw = (rowCost / totalCost) * 100;
  if (raw < 0) {
    return 0;
  }
  if (raw > 100) {
    return 100;
  }
  return Math.round(raw);
}
