/**
 * @vitest-environment jsdom
 */
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Mock the hook directly so banner tests don't drag in polling/fetch logic
// from BillingStatusProvider.
vi.mock('../../contexts/BillingStatusContext', () => ({
  useBillingStatus: vi.fn(),
}));

import PaymentBanner from '../PaymentBanner';
import { useBillingStatus } from '../../contexts/BillingStatusContext';

describe('PaymentBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when billing is healthy', () => {
    useBillingStatus.mockReturnValue({
      paymentFailedAt: null,
      graceDeadline: null,
      servicePaused: false,
      loading: false,
    });

    const { container } = render(<PaymentBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing while loading', () => {
    useBillingStatus.mockReturnValue({
      paymentFailedAt: null,
      graceDeadline: null,
      servicePaused: false,
      loading: true,
    });

    const { container } = render(<PaymentBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('renders warning alert with formatted date during grace period', () => {
    const graceDeadline = '2026-05-15T00:00:00Z';
    useBillingStatus.mockReturnValue({
      paymentFailedAt: '2026-05-01T00:00:00Z',
      graceDeadline,
      servicePaused: false,
      loading: false,
    });

    render(<PaymentBanner />);

    const alert = screen.getByRole('alert');
    expect(alert).toBeInTheDocument();
    const expectedDate = new Date(graceDeadline).toLocaleDateString();
    expect(alert).toHaveTextContent(`Payment failed — service pauses on ${expectedDate}.`);
  });

  it('renders error alert with recovery copy when service is paused', () => {
    useBillingStatus.mockReturnValue({
      paymentFailedAt: '2026-04-01T00:00:00Z',
      graceDeadline: '2026-04-15T00:00:00Z',
      servicePaused: true,
      loading: false,
    });

    render(<PaymentBanner />);

    const alert = screen.getByRole('alert');
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveTextContent('Payment failed — service paused. Check your email for recovery instructions.');
  });

  it('renders error alert when paused with null graceDeadline', () => {
    useBillingStatus.mockReturnValue({
      paymentFailedAt: '2026-04-01T00:00:00Z',
      graceDeadline: null,
      servicePaused: true,
      loading: false,
    });

    render(<PaymentBanner />);

    const alert = screen.getByRole('alert');
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveTextContent('Payment failed — service paused. Check your email for recovery instructions.');
  });
});
