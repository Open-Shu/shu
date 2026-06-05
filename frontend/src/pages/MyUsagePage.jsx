import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, IconButton, Paper, Skeleton, Stack, Tooltip, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';

import CostByModelTable from '../components/billing/CostByModelTable';
import MyUsageKpiTiles from '../components/billing/MyUsageKpiTiles';
import PersonalKbStatsTile from '../components/billing/PersonalKbStatsTile';
import UpsellCard from '../components/billing/UpsellCard';
import { useMyUsageData } from '../hooks/useMyUsageData';
import { useBillingStatus } from '../contexts/BillingStatusContext';
import { formatBillingPeriod, formatLastUpdated } from '../utils/billingFormatters';

// Code-split the chart so @mui/x-charts (and its d3 vendor bundle) loads only
// when a user opens this route — it stays out of the app's initial bundle.
const MyUsageChart = lazy(() => import('../components/billing/MyUsageChart'));

const SECTION_HEADING_SX = {
  fontWeight: 600,
  letterSpacing: '0.1em',
  color: 'primary.main',
};

function PageHeader({ usageQuery, lastUpdatedAt, onRefresh, timezone }) {
  // Tick every minute so "Last updated X ago" recomputes against the clock
  // even when no other state changed.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  let subtitle;
  if (usageQuery.isLoading) {
    subtitle = <Skeleton width={280} />;
  } else if (usageQuery.isError) {
    subtitle = (
      <Typography variant="body2" color="text.secondary">
        Period unavailable
      </Typography>
    );
  } else if (usageQuery.data?.current_period_unknown) {
    subtitle = (
      <Typography variant="body2" color="text.secondary">
        No active billing period
      </Typography>
    );
  } else {
    subtitle = (
      <Typography variant="body2" color="text.secondary">
        Current billing period:{' '}
        {formatBillingPeriod(usageQuery.data?.period_start, usageQuery.data?.period_end, timezone)}
      </Typography>
    );
  }

  const lastUpdatedAbsolute = lastUpdatedAt ? new Date(lastUpdatedAt).toLocaleString() : 'never';

  return (
    <Stack
      direction={{ xs: 'column', sm: 'row' }}
      alignItems={{ xs: 'flex-start', sm: 'center' }}
      justifyContent="space-between"
      spacing={1}
      sx={{ mb: 2 }}
    >
      <Box>
        <Typography variant="h4" sx={{ fontWeight: 600 }}>
          My Usage
        </Typography>
        {subtitle}
      </Box>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Tooltip title={`Last updated: ${lastUpdatedAbsolute}`} arrow>
          <Typography variant="caption" color="text.secondary">
            Last updated {formatLastUpdated(lastUpdatedAt)}
          </Typography>
        </Tooltip>
        <IconButton size="small" onClick={onRefresh} aria-label="Refresh usage data" disabled={usageQuery.isFetching}>
          <RefreshIcon />
        </IconButton>
      </Stack>
    </Stack>
  );
}

function UsageErrorAlert({ onRefresh }) {
  return (
    <Alert
      severity="error"
      action={
        <Button color="inherit" size="small" onClick={onRefresh}>
          Retry
        </Button>
      }
    >
      We couldn&apos;t load your usage data. Please try again.
    </Alert>
  );
}

/**
 * Per-user "My Usage" dashboard (SHU-844).
 *
 * Self-service counterpart to the admin Cost & Usage dashboard (SHU-733),
 * available to every role from the user menu. All usage is scoped to the
 * requesting user via `/billing/usage/me`; plan/pool context comes from
 * BillingStatusContext. Reuses the Cost by Model table, KPI tile, and
 * formatters from the admin dashboard.
 */
function MyUsagePage() {
  const { usage, modelsMap, refetch, lastUpdatedAt } = useMyUsageData();
  const { totalGrantAmount, remainingGrantAmount } = useBillingStatus();

  // Resolve the user's local timezone once for the period label.
  const timezone = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
    } catch {
      return null;
    }
  }, []);

  // Shared-pool context from the billing status poll. null when CP isn't
  // configured → the KPI tiles degrade to volume-only.
  const pool =
    typeof totalGrantAmount === 'number'
      ? { total: totalGrantAmount, remaining: remainingGrantAmount ?? totalGrantAmount }
      : null;

  const isPeriodKnown = Boolean(usage.data) && !usage.data.current_period_unknown;

  return (
    <Box sx={{ p: { xs: 1.5, sm: 3 }, maxWidth: 1400, mx: 'auto' }}>
      <PageHeader usageQuery={usage} lastUpdatedAt={lastUpdatedAt} onRefresh={refetch} timezone={timezone} />

      <Stack spacing={3}>
        <UpsellCard />

        <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack spacing={2}>
            <Typography variant="overline" sx={SECTION_HEADING_SX}>
              Summary
            </Typography>
            {usage.isError ? (
              <UsageErrorAlert onRefresh={refetch} />
            ) : (
              <MyUsageKpiTiles usageData={usage.data} isLoading={usage.isLoading} pool={pool} />
            )}
          </Stack>
        </Paper>

        <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack spacing={2}>
            <Typography variant="overline" sx={SECTION_HEADING_SX}>
              Usage Over Time
            </Typography>
            {usage.isError ? (
              <UsageErrorAlert onRefresh={refetch} />
            ) : usage.isLoading ? (
              <Skeleton variant="rounded" height={320} />
            ) : isPeriodKnown ? (
              <Suspense fallback={<Skeleton variant="rounded" height={320} />}>
                <MyUsageChart byDay={usage.data?.by_day} modelsMap={modelsMap} />
              </Suspense>
            ) : (
              <Box sx={{ py: 4, textAlign: 'center' }}>
                <Typography variant="body2" color="text.secondary">
                  Usage trends will appear here once a billing period is active.
                </Typography>
              </Box>
            )}
          </Stack>
        </Paper>

        <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack spacing={2}>
            <Typography variant="overline" sx={SECTION_HEADING_SX}>
              Cost by Model
            </Typography>
            {usage.isError ? (
              <UsageErrorAlert onRefresh={refetch} />
            ) : (
              <CostByModelTable usageQuery={usage} modelsMap={modelsMap} />
            )}
          </Stack>
        </Paper>

        <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack spacing={2}>
            <Typography variant="overline" sx={SECTION_HEADING_SX}>
              Your Personal Knowledge Base
            </Typography>
            <PersonalKbStatsTile />
          </Stack>
        </Paper>
      </Stack>
    </Box>
  );
}

export default MyUsagePage;
