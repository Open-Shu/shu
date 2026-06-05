/**
 * Tests for UpsellCard (SHU-844).
 *
 * Conditional surface: renders only for capped plans (trial or free-tier
 * hard_cap) once usage reaches >= 80% of the shared pool. Reads context via
 * useBillingStatus.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../../contexts/BillingStatusContext', () => ({
  useBillingStatus: vi.fn(),
}));
vi.mock('../../../utils/log', () => ({
  default: { warn: vi.fn(), info: vi.fn(), error: vi.fn(), debug: vi.fn() },
}));

import { useBillingStatus } from '../../../contexts/BillingStatusContext';
import UpsellCard from '../UpsellCard';

const status = (overrides = {}) => ({
  isTrial: false,
  hardCap: false,
  totalGrantAmount: null,
  remainingGrantAmount: null,
  loading: false,
  ...overrides,
});

describe('UpsellCard', () => {
  beforeEach(() => vi.clearAllMocks());

  describe('hidden', () => {
    it('while loading', () => {
      useBillingStatus.mockReturnValue(status({ loading: true }));
      const { container } = render(<UpsellCard />);
      expect(container.firstChild).toBeNull();
    });

    it('when not on a capped plan', () => {
      useBillingStatus.mockReturnValue(status({ totalGrantAmount: 100, remainingGrantAmount: 5 }));
      const { container } = render(<UpsellCard />);
      expect(container.firstChild).toBeNull();
    });

    it('when there is no pool (totalGrantAmount null)', () => {
      useBillingStatus.mockReturnValue(status({ isTrial: true, totalGrantAmount: null }));
      const { container } = render(<UpsellCard />);
      expect(container.firstChild).toBeNull();
    });

    it('when the pool is zero', () => {
      useBillingStatus.mockReturnValue(status({ isTrial: true, totalGrantAmount: 0, remainingGrantAmount: 0 }));
      const { container } = render(<UpsellCard />);
      expect(container.firstChild).toBeNull();
    });

    it('when usage is below the 80% threshold', () => {
      // 100 total, 30 remaining → 70% used
      useBillingStatus.mockReturnValue(status({ isTrial: true, totalGrantAmount: 100, remainingGrantAmount: 30 }));
      const { container } = render(<UpsellCard />);
      expect(container.firstChild).toBeNull();
    });
  });

  describe('shown', () => {
    it('at exactly 80% on a trial (info wording)', () => {
      useBillingStatus.mockReturnValue(status({ isTrial: true, totalGrantAmount: 100, remainingGrantAmount: 20 }));
      render(<UpsellCard />);
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText(/used 80% of your plan/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /see upgrade options/i })).toBeInTheDocument();
    });

    it('when exhausted (>= 100%) with allowance wording', () => {
      useBillingStatus.mockReturnValue(status({ isTrial: true, totalGrantAmount: 100, remainingGrantAmount: 0 }));
      render(<UpsellCard />);
      expect(screen.getByText(/used your full plan allowance/i)).toBeInTheDocument();
    });

    it('on a free-tier hard_cap plan (not trial) at >= 80%', () => {
      // hardCap path, 100 total, 10 remaining → 90% used
      useBillingStatus.mockReturnValue(status({ hardCap: true, totalGrantAmount: 100, remainingGrantAmount: 10 }));
      render(<UpsellCard />);
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText(/used 90% of your plan/)).toBeInTheDocument();
    });

    it('shows the pool total and a progress bar', () => {
      useBillingStatus.mockReturnValue(status({ isTrial: true, totalGrantAmount: 250.5, remainingGrantAmount: 10 }));
      render(<UpsellCard />);
      // The amount is a separate JSX expression node, so assert on the
      // alert's concatenated text content rather than a single text node.
      expect(screen.getByRole('alert')).toHaveTextContent('shared $250.50 usage pool');
      expect(screen.getByRole('progressbar')).toBeInTheDocument();
    });
  });
});
