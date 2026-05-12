import React, { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  LinearProgress,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useBillingStatus } from '../contexts/BillingStatusContext';
import { useAuth } from '../hooks/useAuth';
import { billingAPI } from '../services/api';
import log from '../utils/log';

// Server-side has no token check; this constant is purely the UI
// gate against accidental cancel clicks. Server enforces admin role.
const CANCEL_CONFIRM_PHRASE = 'CONFIRM';

/**
 * TrialBanner surfaces trial-state to the user with the budget bar and
 * exit actions (upgrade-now / cancel-trial).
 *
 * Renders only while `is_trial=true`. Styled to match PaymentBanner
 * (full-width, in-flow, no overlap) so the two banners share a visual
 * language for billing-status surfaces.
 */
const TrialBanner = () => {
  const { isTrial, trialDeadline, totalGrantAmount, remainingGrantAmount, seatPriceUsd, userCount, loading, refetch } =
    useBillingStatus();
  const { canManageUsers } = useAuth();

  // Upgrade-now flow: single confirm dialog.
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [upgradeSubmitting, setUpgradeSubmitting] = useState(false);
  const [upgradeError, setUpgradeError] = useState('');

  // Cancel-trial flow: two-step. First a warning dialog, then a typed-
  // CONFIRM input. Tracked as a 'closed' | 'warning' | 'typed' state so
  // both dialogs share a single state transition path.
  const [cancelStep, setCancelStep] = useState('closed');
  const [cancelInput, setCancelInput] = useState('');
  const [cancelSubmitting, setCancelSubmitting] = useState(false);
  const [cancelError, setCancelError] = useState('');

  // Non-admins don't see the trial banner. Spec calls for "trial state always
  // visible to admins"; non-admins have no exit actions available (endpoints
  // are admin-gated), so the banner would be informational dead weight for
  // them — and risks them clicking buttons that 403 silently.
  if (loading || !isTrial || !canManageUsers()) {
    return null;
  }

  // Defensive: a brand-new tenant may load before the backend has
  // populated grant values. Format gracefully rather than rendering "NaN".
  const total = totalGrantAmount ?? 0;
  // Clamp displayed remaining to [0, total]: a state desync (e.g., grant
  // top-up before the cache catches up, or freshly-changed total below
  // tracked remaining) would otherwise render absurd copy like
  // "$80 of $50 remaining."
  const remaining = Math.min(Math.max(remainingGrantAmount ?? 0, 0), total);
  const used = Math.max(total - remaining, 0);
  // Progress shows usage filling up toward the cap. Bar floors to 0 / caps
  // at 100 so a temporary state-skew (used > total) doesn't render bizarrely.
  const percentUsed = total > 0 ? Math.min(Math.max((used / total) * 100, 0), 100) : 0;

  const deadlineText = trialDeadline ? new Date(trialDeadline).toLocaleDateString() : null;

  // Projected monthly cost after conversion = current seat count × per-seat
  // price. Customers add users freely during trial; this number is what they
  // commit to when upgrading at the current seat count.
  const projectedMonthly = seatPriceUsd !== null && userCount > 0 ? userCount * seatPriceUsd : null;

  // Pull a user-facing message off an axios-style error without leaking
  // arbitrary backend details. Falls back to the exception's own message
  // so the dialog never renders an empty string on failure.
  const errorMessage = (err) => err?.response?.data?.detail || err?.message || 'Request failed. Try again.';

  const handleUpgradeConfirm = async () => {
    setUpgradeError('');
    setUpgradeSubmitting(true);
    try {
      await billingAPI.upgradeNow();
      setUpgradeOpen(false);
      // Pull fresh state immediately rather than waiting for the next
      // 60s polling tick — the banner should disappear right after the
      // action succeeds.
      await refetch();
    } catch (err) {
      log.error('Upgrade-now failed:', err);
      setUpgradeError(errorMessage(err));
    } finally {
      setUpgradeSubmitting(false);
    }
  };

  const handleCancelSubmit = async () => {
    setCancelError('');
    setCancelSubmitting(true);
    try {
      await billingAPI.cancelTrial();
      setCancelStep('closed');
      setCancelInput('');
      await refetch();
    } catch (err) {
      log.error('Cancel-trial failed:', err);
      setCancelError(errorMessage(err));
    } finally {
      setCancelSubmitting(false);
    }
  };

  const closeCancel = () => {
    setCancelStep('closed');
    setCancelInput('');
    setCancelError('');
  };

  const closeUpgrade = () => {
    setUpgradeOpen(false);
    setUpgradeError('');
  };

  // Submit is enabled only on an exact-match — strict equality, not
  // case-insensitive, to keep the gate predictable for screen-reader users
  // who'd otherwise hit a moving target.
  const cancelSubmitDisabled = cancelInput !== CANCEL_CONFIRM_PHRASE || cancelSubmitting;

  const sx = {
    flexShrink: 0,
    borderRadius: 0,
    fontSize: '1.05rem',
    fontWeight: 500,
    py: 2,
    '& .MuiAlert-message': { width: '100%' },
    '& .MuiAlert-icon': { fontSize: '1.75rem', alignItems: 'center' },
  };

  return (
    <>
      <Alert severity="info" sx={sx}>
        <Stack spacing={1.5}>
          <Box>
            <Typography variant="body1" component="span" sx={{ fontWeight: 600 }}>
              Free trial
            </Typography>
            {deadlineText && (
              <Typography variant="body2" component="span" sx={{ ml: 1 }}>
                · ends {deadlineText}
              </Typography>
            )}
          </Box>

          <Box>
            <Typography variant="body2">
              ${remaining.toFixed(2)} of ${total.toFixed(2)} usage remaining
            </Typography>
            <LinearProgress
              variant="determinate"
              value={percentUsed}
              aria-label="Trial usage progress"
              sx={{ mt: 0.5, height: 8, borderRadius: 1 }}
            />
          </Box>

          {/* Shared-pool messaging per R10.AC2: customers can invite teammates
             during trial without multiplying the budget. Worth saying explicitly
             because the natural assumption is per-seat allocation. */}
          <Typography variant="body2">
            All users share a single ${total.toFixed(2)} pool — adding teammates does not multiply the budget.
          </Typography>

          {projectedMonthly !== null && (
            <Typography variant="body2">
              After trial: ~${projectedMonthly.toFixed(2)}/month at {userCount} {userCount === 1 ? 'seat' : 'seats'}.
            </Typography>
          )}

          <Stack direction="row" spacing={1}>
            <Button variant="contained" size="small" aria-label="Upgrade now" onClick={() => setUpgradeOpen(true)}>
              Upgrade now
            </Button>
            <Button variant="outlined" size="small" aria-label="Cancel trial" onClick={() => setCancelStep('warning')}>
              Cancel trial
            </Button>
          </Stack>
        </Stack>
      </Alert>

      {/* Upgrade-now: single confirm. The financial commitment is shown
         inline so the customer sees what they're agreeing to. */}
      <Dialog
        open={upgradeOpen}
        onClose={() => !upgradeSubmitting && closeUpgrade()}
        aria-labelledby="upgrade-now-title"
      >
        <DialogTitle id="upgrade-now-title">Upgrade now?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This ends your trial immediately and starts your paid subscription.
            {projectedMonthly !== null && (
              <>
                {' '}
                You will be billed approximately ${projectedMonthly.toFixed(2)}/month for {userCount}{' '}
                {userCount === 1 ? 'seat' : 'seats'} at the current rate. To reduce the cost remove some active users.
              </>
            )}
          </DialogContentText>
          {upgradeError && (
            <Alert severity="error" role="alert" sx={{ mt: 2 }}>
              {upgradeError}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={closeUpgrade} disabled={upgradeSubmitting} aria-label="Cancel upgrade">
            Not yet
          </Button>
          <Button
            onClick={handleUpgradeConfirm}
            disabled={upgradeSubmitting}
            variant="contained"
            aria-label="Confirm upgrade"
          >
            Upgrade
          </Button>
        </DialogActions>
      </Dialog>

      {/* Cancel-trial: two-step. Step 1 = "are you sure" warning.
         Step 2 = typed-CONFIRM gate. Both required to fire the POST. */}
      <Dialog
        open={cancelStep === 'warning'}
        onClose={() => !cancelSubmitting && closeCancel()}
        aria-labelledby="cancel-warning-title"
      >
        <DialogTitle id="cancel-warning-title">Cancel your trial?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This immediately ends your trial and disables access. Cancellation cannot be undone — you would need to
            start over with a fresh signup.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeCancel} aria-label="Keep trial">
            Keep trial
          </Button>
          <Button onClick={() => setCancelStep('typed')} color="warning" aria-label="Continue to cancel">
            Continue
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={cancelStep === 'typed'}
        onClose={() => !cancelSubmitting && closeCancel()}
        aria-labelledby="cancel-typed-title"
      >
        <DialogTitle id="cancel-typed-title">Confirm cancellation</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Type <strong>{CANCEL_CONFIRM_PHRASE}</strong> below to confirm. This action is permanent.
          </DialogContentText>
          <TextField
            autoFocus
            fullWidth
            size="small"
            value={cancelInput}
            onChange={(e) => setCancelInput(e.target.value)}
            inputProps={{ 'aria-label': 'Type CONFIRM to enable cancellation' }}
            disabled={cancelSubmitting}
          />
          {cancelError && (
            <Alert severity="error" role="alert" sx={{ mt: 2 }}>
              {cancelError}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={closeCancel} disabled={cancelSubmitting} aria-label="Back out of cancellation">
            Never mind
          </Button>
          <Button
            onClick={handleCancelSubmit}
            disabled={cancelSubmitDisabled}
            color="error"
            variant="contained"
            aria-label="Submit cancellation"
          >
            Cancel trial
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default TrialBanner;
