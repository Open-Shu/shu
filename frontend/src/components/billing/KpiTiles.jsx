import { Card, CardContent, Grid, Skeleton, Tooltip, Typography } from '@mui/material';

import { formatCurrency, formatCompactTokens, formatFullTokens } from '../../utils/billingFormatters';

const PLACEHOLDER = '—';

function KpiTile({ label, value, ariaLabel, tooltip }) {
  const content = (
    <Card variant="outlined" sx={{ height: '100%' }}>
      <CardContent>
        <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: '0.08em' }}>
          {label}
        </Typography>
        <Typography variant="h4" sx={{ fontWeight: 600, mt: 0.5 }} aria-label={ariaLabel}>
          {value}
        </Typography>
      </CardContent>
    </Card>
  );

  if (!tooltip) {
    return content;
  }
  return (
    <Tooltip title={tooltip} arrow>
      {content}
    </Tooltip>
  );
}

/**
 * KPI summary tiles for the Cost & Usage page.
 *
 * - When loading: four skeleton tiles of the same dimensions as loaded tiles.
 * - When `current_period_unknown` is true: tiles render the em-dash placeholder.
 * - Otherwise: tiles render Total Cost, Input Tokens, Output Tokens, and a
 *   computed Requests count (sum of `by_model[].request_count`).
 */
function KpiTiles({ usageQuery }) {
  const isLoading = usageQuery.isLoading;
  const isPeriodUnknown = !!usageQuery.data && usageQuery.data.current_period_unknown === true;
  const data = usageQuery.data || {};

  const totalCost = isPeriodUnknown ? null : data.total_cost_usd;
  const totalInput = isPeriodUnknown ? null : data.total_input_tokens;
  const totalOutput = isPeriodUnknown ? null : data.total_output_tokens;
  const totalRequests = isPeriodUnknown
    ? null
    : Array.isArray(data.by_model)
      ? data.by_model.reduce((sum, row) => sum + (row.request_count || 0), 0)
      : 0;

  const tiles = [
    {
      key: 'cost',
      label: 'Total Cost',
      value: isPeriodUnknown ? PLACEHOLDER : formatCurrency(totalCost),
      ariaLabel: isPeriodUnknown ? 'Total cost: not available' : `Total cost: ${formatCurrency(totalCost)}`,
    },
    {
      key: 'input',
      label: 'Input Tokens',
      value: isPeriodUnknown ? PLACEHOLDER : formatCompactTokens(totalInput),
      ariaLabel: isPeriodUnknown ? 'Input tokens: not available' : `Input tokens: ${formatFullTokens(totalInput)}`,
      tooltip: isPeriodUnknown ? null : formatFullTokens(totalInput),
    },
    {
      key: 'output',
      label: 'Output Tokens',
      value: isPeriodUnknown ? PLACEHOLDER : formatCompactTokens(totalOutput),
      ariaLabel: isPeriodUnknown ? 'Output tokens: not available' : `Output tokens: ${formatFullTokens(totalOutput)}`,
      tooltip: isPeriodUnknown ? null : formatFullTokens(totalOutput),
    },
    {
      key: 'requests',
      label: 'Requests',
      value: isPeriodUnknown ? PLACEHOLDER : formatFullTokens(totalRequests),
      ariaLabel: isPeriodUnknown ? 'Requests: not available' : `Requests: ${formatFullTokens(totalRequests)}`,
    },
  ];

  return (
    <Grid container spacing={2}>
      {tiles.map((tile) => (
        <Grid item xs={12} sm={6} md={3} key={tile.key}>
          {isLoading ? (
            <Skeleton variant="rounded" height={88} />
          ) : (
            <KpiTile label={tile.label} value={tile.value} ariaLabel={tile.ariaLabel} tooltip={tile.tooltip} />
          )}
        </Grid>
      ))}
    </Grid>
  );
}

export default KpiTiles;
