import { Box, Card, CardContent, Grid, LinearProgress, Skeleton, Typography } from '@mui/material';

import { formatCurrency, INCLUDED_USAGE_PER_SEAT_USD, OVERAGE_MARKUP_MULTIPLIER } from '../../utils/billingFormatters';

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
 * Tiles are scoped to the financial story:
 *   1. Usage Cost — provider cost incurred this period
 *   2. Included Allowance — seats × $50, the per-period included pool
 *   3. Used — % of allowance consumed, with progress bar
 *   4. Overage — $0 within allowance, otherwise the dollar overage with
 *      the +30% upcharged amount as a sub-line
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
  const allowance = seats !== null ? seats * INCLUDED_USAGE_PER_SEAT_USD : null;

  const haveBudgetMath = usageCost !== null && allowance !== null && allowance > 0;
  const usedPercent = haveBudgetMath ? Math.round((usageCost / allowance) * 100) : null;
  const overage = haveBudgetMath ? Math.max(0, usageCost - allowance) : null;
  const overageCharge = overage !== null ? overage * OVERAGE_MARKUP_MULTIPLIER : null;

  const tiles = [
    {
      key: 'cost',
      label: 'Usage Cost',
      value: usageCost === null ? PLACEHOLDER : formatCurrency(usageCost),
      ariaLabel: usageCost === null ? 'Usage cost: not available' : `Usage cost: ${formatCurrency(usageCost)}`,
    },
    {
      key: 'allowance',
      label: 'Included Allowance',
      value: allowance === null ? PLACEHOLDER : formatCurrency(allowance),
      ariaLabel:
        allowance === null ? 'Included allowance: not available' : `Included allowance: ${formatCurrency(allowance)}`,
      subline:
        seats !== null
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
      key: 'overage',
      label: 'Overage',
      value: overage === null ? PLACEHOLDER : formatCurrency(overage),
      ariaLabel: overage === null ? 'Overage: not available' : `Overage: ${formatCurrency(overage)}`,
      valueColor: overage !== null && overage > 0 ? 'error.main' : undefined,
      subline:
        overage === null
          ? null
          : overage > 0
            ? `charged at ${formatCurrency(overageCharge)} (+30%)`
            : 'within allowance',
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
