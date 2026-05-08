import React from 'react';
import { Alert } from '@mui/material';
import { useBillingStatus } from '../contexts/BillingStatusContext';

/**
 * PaymentBanner surfaces billing failure state to the user.
 *
 * Recovery channel is email (Stripe today, control plane later) — there is
 * intentionally no CTA or Stripe Portal link here.
 */
const PaymentBanner = () => {
  const { paymentFailedAt, graceDeadline, servicePaused, loading } = useBillingStatus();

  if (loading || (!paymentFailedAt && !servicePaused)) {
    return null;
  }

  // Defensive: paused state may arrive without a deadline (see task 11e).
  // Falling back to null keeps formatting from blowing up on `new Date(null)`.
  const formattedDate = graceDeadline ? new Date(graceDeadline).toLocaleDateString() : null;

  // Rendered in normal flow inside the flex-column wrapper in App.js, so it
  // pushes the routed layout down instead of overlapping it. Banner height
  // is implicit (driven by py + content); the parent flex layout absorbs
  // the change so no magic-number coupling is needed.
  const sx = {
    flexShrink: 0,
    borderRadius: 0,
    fontSize: '1.05rem',
    fontWeight: 500,
    py: 2,
    '& .MuiAlert-message': { width: '100%', textAlign: 'center' },
    '& .MuiAlert-icon': { fontSize: '1.75rem', alignItems: 'center' },
  };

  if (servicePaused) {
    return (
      <Alert severity="error" sx={sx}>
        Payment failed — service paused. Check your email for recovery instructions.
      </Alert>
    );
  }

  return (
    <Alert severity="warning" sx={sx}>
      Payment failed — service pauses on {formattedDate}.
    </Alert>
  );
};

export default PaymentBanner;
