import { useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, Skeleton, Snackbar, Stack } from '@mui/material';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';

import { billingAPI, extractDataFromResponse } from '../../services/api';
import log from '../../utils/log';
import { formatDateInTimezone } from '../../utils/timezoneFormatter';

/**
 * Pure helper that maps a `/billing/subscription` response to one of:
 *   'hidden' | 'healthy' | 'unhealthy:payment_failed'
 *   | 'unhealthy:past_due' | 'unhealthy:canceled'
 *   | 'unhealthy:will_not_renew' | 'unhealthy:incomplete'
 *
 * Precedence (highest first):
 *   current_period_unknown → hidden
 *   payment_failed_at set → payment_failed (wins over cancel_at_period_end)
 *   status canceled → canceled
 *   status past_due → past_due
 *   status incomplete/incomplete_expired → incomplete
 *   status active + cancel_at_period_end → will_not_renew
 *   status active + clean → healthy
 *   anything else → unhealthy:incomplete (defensive catch-all)
 */
export function selectSubscriptionView(state) {
  if (!state || state.current_period_unknown) {
    return 'hidden';
  }
  if (state.payment_failed_at) {
    return 'unhealthy:payment_failed';
  }
  const status = state.subscription_status;
  if (status === 'canceled') {
    return 'unhealthy:canceled';
  }
  if (status === 'past_due') {
    return 'unhealthy:past_due';
  }
  if (status === 'incomplete' || status === 'incomplete_expired') {
    return 'unhealthy:incomplete';
  }
  if (status === 'active' && state.cancel_at_period_end) {
    return 'unhealthy:will_not_renew';
  }
  if (status === 'active') {
    return 'healthy';
  }
  return 'unhealthy:incomplete';
}

function unhealthyAlertConfig(view, state, timezone) {
  const periodEnd = state?.current_period_end
    ? formatDateInTimezone(state.current_period_end, timezone, 'MMM d, yyyy')
    : 'the end of the period';
  const failedAt = state?.payment_failed_at
    ? formatDateInTimezone(state.payment_failed_at, timezone, 'MMM d, yyyy')
    : null;

  switch (view) {
    case 'unhealthy:payment_failed':
      return {
        severity: 'error',
        message: failedAt
          ? `Payment failed on ${failedAt}. Update your payment method to avoid service interruption.`
          : 'Payment failed. Update your payment method to avoid service interruption.',
      };
    case 'unhealthy:past_due':
      return {
        severity: 'error',
        message: 'Subscription is past due. Update your payment method to avoid service interruption.',
      };
    case 'unhealthy:canceled':
      return {
        severity: 'error',
        message: `Subscription canceled. Service ends ${periodEnd}.`,
      };
    case 'unhealthy:will_not_renew':
      return {
        severity: 'warning',
        message: `Subscription will not renew. Service ends ${periodEnd}.`,
      };
    case 'unhealthy:incomplete':
    default:
      return {
        severity: 'error',
        message: 'Subscription setup is incomplete.',
      };
  }
}

function ManageInStripeButton({ size = 'small', variant = 'outlined' }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleClick = async () => {
    setLoading(true);
    try {
      const response = await billingAPI.getPortalUrl();
      const data = extractDataFromResponse(response);
      const url = data?.url;
      if (!url) {
        throw new Error('Portal URL missing from response');
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      log.error('Failed to open Stripe portal', err);
      setError('Could not open the billing portal. Try again in a moment.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Button
        size={size}
        variant={variant}
        onClick={handleClick}
        disabled={loading}
        endIcon={loading ? <CircularProgress size={14} /> : <OpenInNewIcon fontSize="small" />}
        aria-label="Manage subscription in Stripe"
      >
        Manage in Stripe
      </Button>
      <Snackbar
        open={Boolean(error)}
        autoHideDuration={4000}
        onClose={() => setError(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert severity="error" onClose={() => setError(null)} sx={{ width: '100%' }}>
          {error}
        </Alert>
      </Snackbar>
    </>
  );
}

/**
 * Subscription health strip for the Cost & Usage page.
 *
 * Renders one of:
 *   - nothing (hidden) when no billing period is active
 *   - a calm healthy strip with status chip + seat count + Manage button
 *   - a full-width Alert with the appropriate severity and copy
 */
function SubscriptionStrip({ subscriptionQuery, timezone }) {
  if (subscriptionQuery.isLoading) {
    return <Skeleton variant="rounded" height={40} sx={{ mb: 0 }} />;
  }
  if (subscriptionQuery.isError) {
    log.warn('Subscription query failed; hiding subscription strip.');
    return null;
  }

  const state = subscriptionQuery.data;
  const view = selectSubscriptionView(state);

  if (view === 'hidden') {
    return null;
  }

  if (view === 'healthy') {
    const seats = typeof state.quantity === 'number' ? state.quantity : null;
    return (
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
        sx={{ rowGap: 1 }}
        data-testid="subscription-strip-healthy"
      >
        <Chip label="Active" color="success" size="small" />
        {seats !== null && <Chip label={`${seats} seats`} variant="outlined" size="small" />}
        <Box sx={{ flexGrow: 1 }} />
        <ManageInStripeButton />
      </Stack>
    );
  }

  const { severity, message } = unhealthyAlertConfig(view, state, timezone);
  return (
    <Alert
      severity={severity}
      action={<ManageInStripeButton size="small" />}
      data-testid="subscription-strip-unhealthy"
    >
      {message}
    </Alert>
  );
}

export default SubscriptionStrip;
