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
  IconButton,
  LinearProgress,
  Popover,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { useBillingStatus } from '../contexts/BillingStatusContext';
import { useAuth } from '../hooks/useAuth';
import { billingAPI } from '../services/api';
import log from '../utils/log';

// Server-side has no token check; this constant is purely the UI
// gate against accidental cancel clicks. Server enforces admin role.
const CANCEL_CONFIRM_PHRASE = 'CONFIRM';

// Percent of the trial grant consumed at which the banner flips to a warning
// hue (remaining text + mini-bar). Tunable: lower it to warn earlier. $0
// remaining always counts as exhausted regardless of this threshold.
const TRIAL_USAGE_WARNING_PERCENT = 90;

/**
 * TrialBanner surfaces trial-state to the user as a single compact row:
 * "Free trial" label, remaining/total budget, an inline mini usage bar, the
 * deadline, and the exit actions (upgrade-now / cancel-trial). The secondary
 * detail (shared-pool note + projected post-trial cost) lives behind a
 * click-to-open info popover so the banner stays one line tall (SHU-822).
 *
 * Renders only while `is_trial=true`. Full-width and in-flow like PaymentBanner
 * so the two share a billing-status visual language.
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

  // Detail popover (shared-pool note + post-trial estimate). Driven by an
  // anchorEl so it closes cleanly when the banner unmounts after upgrade/cancel
  // — a dangling anchor would otherwise mis-position or warn.
  const [detailAnchor, setDetailAnchor] = useState(null);
  const detailOpen = Boolean(detailAnchor);

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

  // At/near the cap, switch the remaining text + mini-bar to a warning hue so
  // the paywall moment is unmistakable in a slim banner. Guarded on total > 0
  // so the cold-start "$0 of $0" state (grants not yet populated) doesn't false
  // -trigger. The text emphasis survives even when the mini-bar is hidden on
  // narrow widths.
  const exhausted = total > 0 && (remaining <= 0 || percentUsed >= TRIAL_USAGE_WARNING_PERCENT);

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
      // Close the detail popover before refetch flips is_trial false and
      // unmounts the banner, so its anchor never dangles.
      setDetailAnchor(null);
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
      // Mirror the upgrade path: drop the popover anchor before the banner
      // unmounts on refetch.
      setDetailAnchor(null);
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

  // Single compact row. py:0.75 + zeroed message padding keep it one line
  // tall; the actions right-align against a flex spacer.
  const sx = {
    flexShrink: 0,
    borderRadius: 0,
    py: 0.75,
    px: { xs: 1.5, sm: 2 },
    '& .MuiAlert-message': { width: '100%', py: 0 },
  };

  return (
    <>
      {/* icon={false}: the row carries its own trailing info button as the
          popover trigger; the info severity tint is the at-a-glance cue. */}
      <Alert severity="info" icon={false} sx={sx}>
        <Stack direction="row" spacing={{ xs: 1, sm: 1.5 }} alignItems="center" sx={{ flexWrap: 'nowrap' }}>
          {/* Text cluster shares a baseline so the larger "Free trial" heading
              lines up with the smaller status text regardless of the font. */}
          <Stack direction="row" spacing={{ xs: 1, sm: 1.5 }} alignItems="baseline" sx={{ minWidth: 0 }}>
            <Typography
              variant="subtitle1"
              component="span"
              sx={{ fontWeight: 700, fontSize: '1.25rem', lineHeight: 1.2, whiteSpace: 'nowrap', flexShrink: 0 }}
            >
              Free trial
            </Typography>

            <Typography
              variant="body2"
              component="span"
              sx={{
                whiteSpace: 'nowrap',
                flexShrink: 0,
                fontWeight: exhausted ? 600 : 400,
                color: exhausted ? 'warning.main' : 'inherit',
              }}
            >
              ${remaining.toFixed(2)} of ${total.toFixed(2)} left
            </Typography>

            {/* Mini-bar + deadline are non-essential; hide them below md so the
                row sheds width BEFORE it would overflow into a scrollbar, not
                only at the sm/mobile breakpoint. alignSelf centers the bar
                against the baseline-aligned text. */}
            <LinearProgress
              variant="determinate"
              value={percentUsed}
              color={exhausted ? 'warning' : 'primary'}
              aria-label="Trial usage progress"
              sx={{
                width: 64,
                height: 6,
                borderRadius: 1,
                flexShrink: 0,
                alignSelf: 'center',
                display: { xs: 'none', md: 'block' },
              }}
            />

            {deadlineText && (
              <Typography
                variant="body2"
                component="span"
                sx={{ whiteSpace: 'nowrap', display: { xs: 'none', md: 'inline' } }}
              >
                · ends {deadlineText}
              </Typography>
            )}
          </Stack>

          {/* Spacer pushes the actions to the right edge of the row. */}
          <Box sx={{ flexGrow: 1 }} />

          <Button
            variant="contained"
            size="small"
            aria-label="Upgrade now"
            onClick={() => setUpgradeOpen(true)}
            sx={{ flexShrink: 0, whiteSpace: 'nowrap' }}
          >
            {/* "Upgrade" on xs, "Upgrade now" on >=sm so the price and CTA don't
                collide on the narrowest phones. aria-label keeps the accessible
                name "Upgrade now" regardless of the visible text. */}
            Upgrade
            <Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>
              &nbsp;now
            </Box>
          </Button>
          {/* Cancel is inline on >=sm; on xs it folds into the popover (below) so
              the narrow row keeps the primary Upgrade CTA on a single line. */}
          <Button
            variant="outlined"
            size="small"
            aria-label="Cancel trial"
            onClick={() => setCancelStep('warning')}
            sx={{ flexShrink: 0, whiteSpace: 'nowrap', display: { xs: 'none', sm: 'inline-flex' } }}
          >
            Cancel trial
          </Button>

          {/* Detail trigger: the shared-pool note + post-trial estimate (both
              mandated by SHU-757) live in the popover so the row stays compact
              while the content stays keyboard- and touch-reachable. */}
          <IconButton
            size="small"
            aria-label="Trial budget details"
            aria-haspopup="dialog"
            aria-expanded={detailOpen}
            aria-controls={detailOpen ? 'trial-budget-detail' : undefined}
            onClick={(e) => setDetailAnchor(e.currentTarget)}
            sx={{ flexShrink: 0 }}
          >
            <InfoOutlinedIcon fontSize="small" />
          </IconButton>
        </Stack>
      </Alert>

      <Popover
        open={detailOpen}
        anchorEl={detailAnchor}
        onClose={() => setDetailAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Stack
          id="trial-budget-detail"
          component="section"
          aria-label="Trial budget details"
          spacing={1}
          sx={{ p: 2, maxWidth: 320 }}
        >
          {/* Deadline also lives here so it stays reachable below the sm
             breakpoint, where it's hidden from the inline row. */}
          {deadlineText && <Typography variant="body2">Trial ends {deadlineText}.</Typography>}
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

          {/* Cancel folds in here on xs (it's inline on >=sm). Close the popover
              first so the confirm dialog doesn't stack behind it. */}
          <Button
            variant="outlined"
            size="small"
            aria-label="Cancel trial"
            onClick={() => {
              setDetailAnchor(null);
              setCancelStep('warning');
            }}
            sx={{ display: { xs: 'inline-flex', sm: 'none' }, alignSelf: 'flex-start', mt: 0.5 }}
          >
            Cancel trial
          </Button>
        </Stack>
      </Popover>

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
