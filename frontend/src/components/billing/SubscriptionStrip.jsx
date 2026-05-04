import { Alert, Box, Button, Chip, Skeleton, Stack } from '@mui/material';
import { useNavigate } from 'react-router-dom';

import log from '../../utils/log';
import { formatDateInTimezone } from '../../utils/timezoneFormatter';

const SEAT_MANAGEMENT_PATH = '/admin/users';

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

function ManageSeatsButton({ size = 'small', variant = 'outlined' }) {
  const navigate = useNavigate();
  return (
    <Button size={size} variant={variant} onClick={() => navigate(SEAT_MANAGEMENT_PATH)} aria-label="Manage seats">
      Manage Seats
    </Button>
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
    // The endpoint exposes the seat count as `user_limit` (renamed from the
    // underlying billing_state.quantity column). Tolerate both shapes so this
    // keeps working if the response contract ever returns to `quantity`.
    const rawSeats = typeof state.user_limit === 'number' ? state.user_limit : state.quantity;
    const seats = typeof rawSeats === 'number' && rawSeats > 0 ? rawSeats : null;
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
        {seats !== null && (
          <Chip label={`${seats} ${seats === 1 ? 'seat' : 'seats'}`} variant="outlined" size="small" />
        )}
        <Box sx={{ flexGrow: 1 }} />
        <ManageSeatsButton />
      </Stack>
    );
  }

  // Unhealthy modes intentionally render no action button. Manage Seats does
  // not help recover from payment failure / past due / cancellation, and the
  // recovery surface for those states is still TBD — see SHU-734 wishlist.
  // The Alert message stands on its own.
  const { severity, message } = unhealthyAlertConfig(view, state, timezone);
  return (
    <Alert severity={severity} data-testid="subscription-strip-unhealthy">
      {message}
    </Alert>
  );
}

export default SubscriptionStrip;
