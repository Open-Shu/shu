import { useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, IconButton, Paper, Skeleton, Stack, Tooltip, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';

import SubscriptionStrip from '../components/billing/SubscriptionStrip';
import KpiTiles from '../components/billing/KpiTiles';
import CostByModelTable from '../components/billing/CostByModelTable';
import { useUsageData } from '../hooks/useUsageData';
import { formatBillingPeriod, formatLastUpdated } from '../utils/billingFormatters';

const SECTION_HEADING_SX = {
  fontWeight: 600,
  letterSpacing: '0.1em',
  color: 'primary.main',
};

function PageHeader({ usageQuery, lastUpdatedAt, onRefresh, timezone }) {
  // Tick every minute so the "Last updated X ago" caption recomputes against
  // the current clock even when no other state has changed. Without this the
  // string stays frozen at whatever it was on the last render.
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
          Cost & Usage
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
      We couldn&apos;t load usage data. Please try again.
    </Alert>
  );
}

/**
 * Admin Cost & Usage dashboard (SHU-733, v1).
 *
 * Composes the four sections defined in the design ticket: header,
 * subscription strip, KPI summary, and Cost by Model. All data is fetched
 * via `useUsageData`; per-section components own their own loading,
 * error, and empty states.
 */
function CostUsagePage() {
  const { usage, subscription, modelsMap, modelsLoading, refetch, lastUpdatedAt } = useUsageData();

  // Resolve the user's local timezone once. v2 may swap this for a user
  // preference if/when one exists.
  const timezone = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
    } catch {
      return null;
    }
  }, []);

  return (
    <Box sx={{ p: { xs: 1.5, sm: 3 }, maxWidth: 1400, mx: 'auto' }}>
      <PageHeader usageQuery={usage} lastUpdatedAt={lastUpdatedAt} onRefresh={refetch} timezone={timezone} />

      <Stack spacing={3}>
        <SubscriptionStrip subscriptionQuery={subscription} timezone={timezone} />

        <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack spacing={2}>
            <Typography variant="overline" sx={SECTION_HEADING_SX}>
              Summary
            </Typography>
            {usage.isError ? (
              <UsageErrorAlert onRefresh={refetch} />
            ) : (
              <KpiTiles usageQuery={usage} subscriptionQuery={subscription} />
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
              <CostByModelTable usageQuery={usage} modelsMap={modelsMap} modelsLoading={modelsLoading} />
            )}
          </Stack>
        </Paper>
      </Stack>
    </Box>
  );
}

export default CostUsagePage;
