import React from 'react';
import { Alert } from '@mui/material';
import { useBillingStatus } from '../contexts/BillingStatusContext';

// One above MUI's modal/drawer layer so the banner sits over fixed AppBars
// and any open drawers — service-paused state must always be visible.
const BANNER_Z_OFFSET = 2;

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

  // Fixed at the top of the viewport with a high z-index — the routed layouts
  // claim `height: 100vh`, so an in-flow banner gets pushed off-screen even
  // though it renders. Fixed positioning sidesteps the layout overhaul that a
  // proper flex-column rewrite would require across UserLayout / AdminLayout.
  const sx = {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    zIndex: (theme) => theme.zIndex.drawer + BANNER_Z_OFFSET,
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
