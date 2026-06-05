import { Grid, LinearProgress, Skeleton, Tooltip } from '@mui/material';

import { KpiTile, pickUsedColor } from './KpiTiles';
import { formatCompactTokens, formatCurrency, formatFullTokens } from '../../utils/billingFormatters';

const PLACEHOLDER = '—';

/**
 * KPI tiles for the per-user My Usage dashboard (SHU-844).
 *
 * Volume tiles (Your Usage Cost / Requests / Tokens) come from the per-user
 * `/billing/usage/me` payload. "Your Usage Cost" is the raw provider cost —
 * the same basis as the reused Cost by Model table — not a marked-up figure
 * (non-admins are not sent the markup multiplier).
 *
 * The Shared Pool tile is tenant-level context from BillingStatusContext
 * (`pool` prop). It renders only when a pool is present (CP configured); when
 * `pool` is null the page degrades to the three volume tiles. It is captioned
 * "across all seats & shared activity" because the pool spans every seat plus
 * system/shared usage, so a user's own cost is expected to be a subset.
 */
export default function MyUsageKpiTiles({ usageData, isLoading, pool }) {
  const data = usageData || {};
  const isPeriodUnknown = data.current_period_unknown === true;

  const cost = isPeriodUnknown ? null : (data.total_cost_usd ?? 0);
  const requests = isPeriodUnknown ? null : (data.request_count ?? 0);
  const totalTokens = isPeriodUnknown ? null : (data.total_input_tokens ?? 0) + (data.total_output_tokens ?? 0);

  const tiles = [
    {
      key: 'cost',
      label: 'Your Usage Cost',
      value: cost === null ? PLACEHOLDER : formatCurrency(cost),
      ariaLabel: cost === null ? 'Your usage cost: not available' : `Your usage cost: ${formatCurrency(cost)}`,
      subline: 'this billing period',
    },
    {
      key: 'requests',
      label: 'Requests',
      value: requests === null ? PLACEHOLDER : formatFullTokens(requests),
      ariaLabel: requests === null ? 'Requests: not available' : `Requests: ${formatFullTokens(requests)}`,
    },
    {
      key: 'tokens',
      label: 'Tokens',
      value:
        totalTokens === null ? (
          PLACEHOLDER
        ) : (
          <Tooltip title={formatFullTokens(totalTokens)} arrow>
            <span aria-label={`Tokens: ${formatFullTokens(totalTokens)}`}>{formatCompactTokens(totalTokens)}</span>
          </Tooltip>
        ),
      ariaLabel: totalTokens === null ? 'Tokens: not available' : undefined,
      subline: 'input + output',
    },
  ];

  // Shared-pool context (tenant-level). Only when CP shipped a positive pool.
  if (pool && Number.isFinite(pool.total) && pool.total > 0) {
    const remaining = Math.min(Math.max(pool.remaining ?? pool.total, 0), pool.total);
    const used = Math.max(pool.total - remaining, 0);
    const percent = Math.min(Math.max(Math.round((used / pool.total) * 100), 0), 100);
    tiles.push({
      key: 'pool',
      label: 'Shared Pool',
      value: `${formatCurrency(used)} / ${formatCurrency(pool.total)}`,
      ariaLabel: `Shared pool: ${formatCurrency(used)} of ${formatCurrency(pool.total)} used`,
      subline: 'across all seats & shared activity',
      bottom: (
        <LinearProgress
          variant="determinate"
          value={percent}
          color={pickUsedColor(percent)}
          aria-label={`${percent}% of shared pool used`}
        />
      ),
    });
  }

  // 4 tiles → 4-up on md; 3 tiles → 3-up so the row stays balanced.
  const md = tiles.length >= 4 ? 3 : 4;

  return (
    <Grid container spacing={2}>
      {tiles.map((tile) => (
        <Grid item xs={12} sm={6} md={md} key={tile.key}>
          {isLoading ? (
            <Skeleton variant="rounded" height={108} />
          ) : (
            <KpiTile
              label={tile.label}
              value={tile.value}
              ariaLabel={tile.ariaLabel}
              subline={tile.subline}
              bottom={tile.bottom}
            />
          )}
        </Grid>
      ))}
    </Grid>
  );
}
