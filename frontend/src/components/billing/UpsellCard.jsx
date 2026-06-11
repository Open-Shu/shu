import { Alert, Box, Button, LinearProgress, Stack, Typography } from '@mui/material';

import { useBillingStatus } from '../../contexts/BillingStatusContext';
import log from '../../utils/log';

// Surface the upsell once the user is most of the way through their pool.
const NEAR_LIMIT_PERCENT = 80;

/**
 * Free-tier / trial upsell surface for My Usage (SHU-844).
 *
 * Renders only for capped plans (trial or free-tier `hard_cap`) once usage
 * reaches NEAR_LIMIT_PERCENT of the shared pool — the moment an upgrade nudge
 * is relevant. Trigger data comes from BillingStatusContext (already polled);
 * the budget math mirrors TrialBanner. The CTA is a stub per the SHU-844 plan:
 * the real conversion flow is future work and there is no billing write path.
 */
export default function UpsellCard() {
  const { isTrial, hardCap, totalGrantAmount, remainingGrantAmount, loading } = useBillingStatus();

  if (loading) {
    return null;
  }

  // Only capped plans get the nudge. Paid tenants with overage billing don't.
  const capped = isTrial || hardCap;
  const total = totalGrantAmount;
  if (!capped || !total || total <= 0) {
    return null;
  }

  const remaining = Math.min(Math.max(remainingGrantAmount ?? 0, 0), total);
  const used = Math.max(total - remaining, 0);
  const percentUsed = Math.min(Math.max((used / total) * 100, 0), 100);
  if (percentUsed < NEAR_LIMIT_PERCENT) {
    return null;
  }

  const exhausted = percentUsed >= 100;
  const percentLabel = Math.round(percentUsed);

  // Stub CTA — intentionally no billing write path. Wire to the real upgrade
  // flow in a follow-up.
  const handleSeePlans = () => {
    log.info('My Usage upsell CTA clicked (stub)');
  };

  return (
    <Alert severity={exhausted ? 'warning' : 'info'} icon={false} sx={{ borderRadius: 2 }}>
      <Stack spacing={1}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          {exhausted ? 'You’ve used your full plan allowance' : `You’ve used ${percentLabel}% of your plan`}
        </Typography>
        <Typography variant="body2">
          Your plan includes a shared ${total.toFixed(2)} usage pool. Upgrade for more usage, more knowledge bases, and
          additional features.
        </Typography>
        <LinearProgress
          variant="determinate"
          value={percentUsed}
          color={exhausted ? 'error' : 'warning'}
          sx={{ height: 8, borderRadius: 1 }}
          aria-label={`${percentLabel}% of plan used`}
        />
        <Box>
          <Button variant="contained" size="small" onClick={handleSeePlans}>
            See upgrade options
          </Button>
        </Box>
      </Stack>
    </Alert>
  );
}
