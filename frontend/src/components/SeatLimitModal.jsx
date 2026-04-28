import React from 'react';
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, Typography, Box } from '@mui/material';

const USD_DECIMALS = 2;

/**
 * Phase-2 consent modal for inline seat charges.
 *
 * Opens when a create-user or activate-user call returns 402 with
 * `error.code === "seat_limit_reached"`. The primary action retries the
 * original request with `X-Seat-Charge-Confirmed: true`, which the
 * backend treats as "admin saw the preview and approved the charge."
 *
 * There is deliberately no Stripe Portal button here — the Portal is
 * locked under SHU-704 H1/H3 and all seat management flows through this
 * inline surface.
 */
// `details.proration.amount_usd` is the full next-invoice delta —
// proration-for-current-period + one extra seat at the recurring rate. Splitting
// it gives the admin a cleaner "now until period end vs. ongoing" picture.
// Both amount fields arrive as decimal strings (e.g. "19.99") so cents survive
// the wire — parse before any arithmetic.
const buildPriceCopy = ({ amountUsd, costPerSeat, periodEndDisplay }) => {
  if (!amountUsd) {
    return null;
  }
  const totalDelta = parseFloat(amountUsd);
  const recurring = typeof costPerSeat === 'string' ? parseFloat(costPerSeat) : NaN;
  const hasRecurringRate = Number.isFinite(recurring) && recurring > 0;
  if (!hasRecurringRate) {
    return `~$${amountUsd} will appear on your next invoice${periodEndDisplay ? ` (${periodEndDisplay})` : ''}.`;
  }
  const prorationOnly = Math.max(0, totalDelta - recurring).toFixed(USD_DECIMALS);
  if (parseFloat(prorationOnly) <= 0) {
    return `~$${costPerSeat}/month${periodEndDisplay ? ` starting ${periodEndDisplay}` : ''}.`;
  }
  return `~$${prorationOnly} prorated${periodEndDisplay ? ` until ${periodEndDisplay}` : ''}, then ~$${costPerSeat}/month thereafter.`;
};

const SeatLimitModal = ({ open, onClose, onConfirm, details, isConfirming }) => {
  const amountUsd = details?.proration?.amount_usd;
  const periodEnd = details?.proration?.period_end;
  const costPerSeat = details?.cost_per_seat_usd;
  const periodEndDisplay = periodEnd ? new Date(periodEnd).toLocaleDateString() : null;
  const priceCopy = buildPriceCopy({ amountUsd, costPerSeat, periodEndDisplay });
  const primaryLabel = amountUsd ? `Add 1 seat for ~$${amountUsd}` : 'Add 1 seat';
  const userLimit = details?.user_limit;
  const nextSeatCount = userLimit !== null && userLimit !== undefined ? userLimit + 1 : '—';

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add a seat to continue</DialogTitle>
      <DialogContent>
        <Box sx={{ pt: 1 }}>
          <Typography variant="body1" gutterBottom>
            Creating this user will add 1 seat to your subscription.
          </Typography>
          {priceCopy && (
            <Typography variant="body1" gutterBottom>
              {priceCopy}
            </Typography>
          )}
          <Typography variant="body2" color="text.secondary">
            Your seat count goes from {userLimit ?? '—'} to {nextSeatCount}.
          </Typography>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} aria-label="Cancel seat charge">
          Cancel
        </Button>
        <Button onClick={onConfirm} variant="contained" disabled={isConfirming} aria-label={primaryLabel}>
          {isConfirming ? 'Adding seat…' : primaryLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default SeatLimitModal;
