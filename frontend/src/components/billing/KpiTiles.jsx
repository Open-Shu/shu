import { Box, Card, CardContent, Grid, LinearProgress, Skeleton, Typography } from '@mui/material';

import { formatCurrency, INCLUDED_USAGE_PER_SEAT_USD, USAGE_MARKUP_MULTIPLIER } from '../../utils/billingFormatters';

const PLACEHOLDER = '—';

function KpiTile({ label, value, ariaLabel, valueColor, subline, bottom }) {
  return (
    <Card variant="outlined" sx={{ height: '100%' }}>
      <CardContent>
        <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: '0.08em' }}>
          {label}
        </Typography>
        <Typography
          variant="h4"
          sx={{ fontWeight: 600, mt: 0.5, color: valueColor || 'text.primary' }}
          aria-label={ariaLabel}
        >
          {value}
        </Typography>
        {subline && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
            {subline}
          </Typography>
        )}
        {bottom && <Box sx={{ mt: 1 }}>{bottom}</Box>}
      </CardContent>
    </Card>
  );
}

/**
 * Read the seat count from a /billing/subscription response. The API
 * exposes the field as `user_limit` (renamed from the underlying
 * billing_state.quantity column). Tolerate both shapes for robustness.
 */
function readSeats(state) {
  if (!state) {
    return null;
  }
  const raw = typeof state.user_limit === 'number' ? state.user_limit : state.quantity;
  return typeof raw === 'number' && raw > 0 ? raw : null;
}

/**
 * Pick the LinearProgress color for the Used tile. Bands match the
 * "are we close to overage?" mental model: green until 80%, warn past
 * 80%, error past 100%. We use the fixed `success` palette here rather
 * than `primary` so the bar reads consistently regardless of the
 * tenant's brand color — a primary-red brand made the safe band look
 * like an alert state.
 *
 * Exported for direct unit testing of the boundary behavior.
 */
export function pickUsedColor(percent) {
  if (percent >= 100) {
    return 'error';
  }
  if (percent >= 80) {
    return 'warning';
  }
  return 'success';
}

/**
 * KPI summary tiles for the Cost & Usage page.
 *
 * Tiles tell the financial story keyed off the SHU-663 epic's pricing
 * model. Stripe's metered Price has a +30% markup baked into its
 * unit_amount_decimal, so every usage event invoices at 1.3× provider
 * cost — not just usage above the included allowance. The tiles
 * reflect that reality:
 *
 *   1. Usage Cost — billed cost (provider cost × markup); the headline
 *      is what counts against the allowance and what hits the invoice.
 *      Sub-line shows the raw provider cost and markup percentage.
 *   2. Included Allowance — per-period pool, sourced from active Stripe
 *      credit grants when available, falling back to seats × $50.
 *   3. Used — billed_cost / allowance, with a color-banded progress bar.
 *   4. Additional Charges — max(0, billed_cost − allowance); the dollar
 *      amount that lands on the invoice beyond the included credit. The
 *      copy stays calm when usage is within allowance.
 *
 * Volume metrics (token counts, request counts) intentionally do not
 * appear here in v1 — they are visible per-row in the Cost by Model
 * table, where the granularity is more useful.
 */
function KpiTiles({ usageQuery, subscriptionQuery }) {
  const isLoading = usageQuery.isLoading || (subscriptionQuery && subscriptionQuery.isLoading);

  const usageData = usageQuery.data || {};
  const subscriptionData = (subscriptionQuery && subscriptionQuery.data) || {};

  const isPeriodUnknown = usageData.current_period_unknown === true;
  const usageCost = isPeriodUnknown ? null : (usageData.total_cost_usd ?? 0);
  const seats = isPeriodUnknown ? null : readSeats(subscriptionData);

  // Prefer the live Stripe markup derived from the metered Price's
  // unit_amount_decimal. Fall back to the constant when the Stripe call
  // fails or the subscription has no metered item (dev / pre-Stripe).
  const apiMarkup = subscriptionData.usage_markup_multiplier;
  const markup = typeof apiMarkup === 'number' && apiMarkup > 0 ? apiMarkup : USAGE_MARKUP_MULTIPLIER;
  const markupPercent = Math.round((markup - 1) * 100);
  const billedCost = usageCost !== null ? usageCost * markup : null;

  // CP ships the active credit-grant total on `total_grant_amount` (SHU-774;
  // previously the tenant fetched it from Stripe as `included_usd_per_period`).
  // String on the wire — parse to number; fall back to the seats × $50
  // estimate when CP hasn't shipped a positive value (no grants issued yet,
  // cold-start default, etc.).
  const apiAllowanceRaw = isPeriodUnknown ? null : subscriptionData.total_grant_amount;
  const apiAllowance = apiAllowanceRaw !== null ? Number(apiAllowanceRaw) : null;
  const allowanceFromApi = Number.isFinite(apiAllowance) && apiAllowance > 0;
  const allowance = allowanceFromApi ? apiAllowance : seats !== null ? seats * INCLUDED_USAGE_PER_SEAT_USD : null;

  const haveBudgetMath = billedCost !== null && allowance !== null && allowance > 0;
  const usedPercent = haveBudgetMath ? Math.round((billedCost / allowance) * 100) : null;
  const additionalCharges = haveBudgetMath ? Math.max(0, billedCost - allowance) : null;

  const tiles = [
    {
      key: 'cost',
      label: 'Usage Cost',
      value: billedCost === null ? PLACEHOLDER : formatCurrency(billedCost),
      ariaLabel: billedCost === null ? 'Usage cost: not available' : `Usage cost: ${formatCurrency(billedCost)}`,
      // Sub-line explains the relationship between raw provider cost and
      // the billed headline. The `usageCost > 0` check covers both "period
      // unknown" (usageCost is null, null > 0 is false) and "zero usage"
      // (nothing meaningful to explain) in one expression.
      subline: usageCost > 0 ? `${formatCurrency(usageCost)} provider cost, billed at +${markupPercent}%` : null,
    },
    {
      key: 'allowance',
      label: 'Included Allowance',
      value: allowance === null ? PLACEHOLDER : formatCurrency(allowance),
      ariaLabel:
        allowance === null ? 'Included allowance: not available' : `Included allowance: ${formatCurrency(allowance)}`,
      subline: allowanceFromApi
        ? 'from active credit grants'
        : seats !== null
          ? `${seats} ${seats === 1 ? 'seat' : 'seats'} × ${formatCurrency(INCLUDED_USAGE_PER_SEAT_USD)}`
          : null,
    },
    {
      key: 'used',
      label: 'Used',
      value: usedPercent === null ? PLACEHOLDER : `${usedPercent}%`,
      ariaLabel: usedPercent === null ? 'Allowance used: not available' : `Allowance used: ${usedPercent}%`,
      bottom:
        usedPercent === null ? null : (
          <LinearProgress
            variant="determinate"
            value={Math.max(0, Math.min(100, usedPercent))}
            color={pickUsedColor(usedPercent)}
            aria-label={`${usedPercent}% of included allowance used`}
          />
        ),
    },
    {
      key: 'additional',
      label: 'Additional Charges',
      value: additionalCharges === null ? PLACEHOLDER : formatCurrency(additionalCharges),
      ariaLabel:
        additionalCharges === null
          ? 'Additional charges: not available'
          : `Additional charges: ${formatCurrency(additionalCharges)}`,
      valueColor: additionalCharges !== null && additionalCharges > 0 ? 'error.main' : undefined,
      subline:
        additionalCharges === null ? null : additionalCharges > 0 ? 'above included allowance' : 'covered by allowance',
    },
  ];

  return (
    <Grid container spacing={2}>
      {tiles.map((tile) => (
        <Grid item xs={12} sm={6} md={3} key={tile.key}>
          {isLoading ? (
            <Skeleton variant="rounded" height={108} />
          ) : (
            <KpiTile
              label={tile.label}
              value={tile.value}
              ariaLabel={tile.ariaLabel}
              valueColor={tile.valueColor}
              subline={tile.subline}
              bottom={tile.bottom}
            />
          )}
        </Grid>
      ))}
    </Grid>
  );
}

export default KpiTiles;
