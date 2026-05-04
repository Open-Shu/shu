import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { vi } from 'vitest';
import SeatLimitModal from '../SeatLimitModal';

const renderModal = (props = {}) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <SeatLimitModal
        open
        onClose={props.onClose || vi.fn()}
        onConfirm={props.onConfirm || vi.fn()}
        details={props.details || {}}
        isConfirming={props.isConfirming || false}
      />
    </ThemeProvider>
  );

describe('SeatLimitModal', () => {
  it('does not render any Stripe Portal button', () => {
    renderModal({
      details: {
        user_limit: 3,
        current_count: 3,
        proration: { amount_usd: '7.50', period_end: '2026-05-01T00:00:00+00:00' },
      },
    });

    // SHU-704 lockdown: portal is gone; make sure no "portal" text leaked in.
    expect(screen.queryByText(/portal/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /open stripe portal/i })).not.toBeInTheDocument();
  });

  it('primary button invokes onConfirm (caller retries with X-Seat-Charge-Confirmed)', () => {
    const onConfirm = vi.fn();
    renderModal({
      onConfirm,
      details: {
        user_limit: 3,
        current_count: 3,
        proration: { amount_usd: '7.50', period_end: '2026-05-01T00:00:00+00:00' },
      },
    });

    const confirm = screen.getByRole('button', { name: /Add 1 seat for ~\$7\.50/i });
    fireEvent.click(confirm);

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('renders generic copy without price when proration is absent', () => {
    renderModal({
      details: { user_limit: 3, current_count: 3 },
    });

    expect(screen.getByRole('button', { name: /^Add 1 seat$/i })).toBeInTheDocument();
    expect(screen.queryByText(/~\$/)).not.toBeInTheDocument();
  });

  it('disables confirm while isConfirming', () => {
    renderModal({
      isConfirming: true,
      details: { user_limit: 3, current_count: 3 },
    });

    // While confirming, the button text changes but the aria-label stays
    // "Add 1 seat" — find the button by its visible text content.
    expect(screen.getByText('Adding seat…').closest('button')).toBeDisabled();
  });

  it('Cancel button invokes onClose', () => {
    const onClose = vi.fn();
    renderModal({ onClose, details: {} });

    fireEvent.click(screen.getByRole('button', { name: /Cancel seat charge/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
