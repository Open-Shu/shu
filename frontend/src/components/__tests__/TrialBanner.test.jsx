/**
 * @vitest-environment jsdom
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Mock the hook directly so banner tests don't drag in polling/fetch logic
// from BillingStatusProvider. Same pattern as PaymentBanner.test.jsx.
vi.mock('../../contexts/BillingStatusContext', () => ({
  useBillingStatus: vi.fn(),
}));

// Banner is gated on admin role; tests default to admin so each case can
// focus on the trial-state branches. The non-admin gate has a dedicated test.
vi.mock('../../hooks/useAuth', () => ({
  useAuth: vi.fn(() => ({ canManageUsers: () => true })),
}));

// Mock the API client — the multi-step flow tests assert the mutation
// fires; the rendering tests above don't care.
vi.mock('../../services/api', () => ({
  billingAPI: {
    upgradeNow: vi.fn(),
    cancelTrial: vi.fn(),
  },
}));

// Quiet log.error during the failure-path tests.
vi.mock('../../utils/log', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}));

import TrialBanner from '../TrialBanner';
import { useBillingStatus } from '../../contexts/BillingStatusContext';
import { useAuth } from '../../hooks/useAuth';
import { billingAPI } from '../../services/api';

const HEALTHY_NON_TRIAL = {
  isTrial: false,
  trialDeadline: null,
  totalGrantAmount: null,
  remainingGrantAmount: null,
  seatPriceUsd: null,
  userCount: 1,
  loading: false,
  refetch: vi.fn(),
};

const ACTIVE_TRIAL = {
  isTrial: true,
  trialDeadline: '2026-06-15T00:00:00Z',
  totalGrantAmount: 5,
  remainingGrantAmount: 3,
  seatPriceUsd: 20,
  userCount: 2,
  loading: false,
  refetch: vi.fn(),
};

describe('TrialBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default each test to an admin viewer. The non-admin case below
    // overrides this — keeping the default in beforeEach means an override
    // in one test can't leak into a later test in the same file.
    useAuth.mockReturnValue({ canManageUsers: () => true });
  });

  it('renders nothing when not in trial', () => {
    useBillingStatus.mockReturnValue(HEALTHY_NON_TRIAL);
    const { container } = render(<TrialBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing while loading', () => {
    useBillingStatus.mockReturnValue({ ...ACTIVE_TRIAL, loading: true });
    const { container } = render(<TrialBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing for non-admin users even when trialing', () => {
    // Spec: "trial state is always visible to admins" — non-admins have no
    // exit actions available (endpoints are admin-gated) and would only see
    // buttons that 403. Hiding the whole banner is the cleaner contract.
    useAuth.mockReturnValue({ canManageUsers: () => false });
    useBillingStatus.mockReturnValue(ACTIVE_TRIAL);
    const { container } = render(<TrialBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('renders trial state with deadline, remaining/total budget, and shared-pool messaging', () => {
    useBillingStatus.mockReturnValue(ACTIVE_TRIAL);
    render(<TrialBanner />);

    const alert = screen.getByRole('alert');
    expect(alert).toBeInTheDocument();

    // Deadline uses locale formatting — assert against the same conversion
    // the component does so the test is locale-agnostic.
    const expectedDate = new Date('2026-06-15T00:00:00Z').toLocaleDateString();
    expect(alert).toHaveTextContent(`ends ${expectedDate}`);

    // Remaining and total dollars surface so the customer can see the budget.
    expect(alert).toHaveTextContent('$3.00 of $5.00 usage remaining');

    // Shared-pool messaging per R10.AC2 — explicit because the natural
    // assumption is per-seat. Asserting the substring rather than the exact
    // sentence lets us tweak wording without churning the test.
    expect(alert).toHaveTextContent(/share a single \$5\.00 pool/);
  });

  it('computes the projected post-trial monthly cost from userCount × seatPriceUsd', () => {
    useBillingStatus.mockReturnValue(ACTIVE_TRIAL);
    render(<TrialBanner />);

    // 2 seats × $20/seat = $40/month
    expect(screen.getByRole('alert')).toHaveTextContent('~$40.00/month at 2 seats');
  });

  it('uses singular "seat" copy when userCount is 1', () => {
    useBillingStatus.mockReturnValue({ ...ACTIVE_TRIAL, userCount: 1 });
    render(<TrialBanner />);

    expect(screen.getByRole('alert')).toHaveTextContent('~$20.00/month at 1 seat');
  });

  it('renders both exit-action buttons with aria labels', () => {
    useBillingStatus.mockReturnValue(ACTIVE_TRIAL);
    render(<TrialBanner />);

    // aria-label is the load-bearing assertion — screen-reader users land
    // on these buttons by label, and the cancel/upgrade distinction is
    // the only safeguard against picking the wrong one.
    expect(screen.getByRole('button', { name: 'Upgrade now' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel trial' })).toBeInTheDocument();
  });

  it('omits the projected cost line when seat-price is missing', () => {
    // Self-hosted / dev or pre-population path — seatPriceUsd may be null
    // before the first poll resolves with a value. Rendering "NaN/month"
    // would be ugly; skipping the line is the safer default.
    useBillingStatus.mockReturnValue({ ...ACTIVE_TRIAL, seatPriceUsd: null });
    render(<TrialBanner />);

    expect(screen.queryByText(/\/month at/)).not.toBeInTheDocument();
  });
});

// Multi-step trial-exit flows. The upgrade-now path is a single confirm;
// cancel-trial is two-step (warning → typed-CONFIRM). Both must call the
// matching API method and then refetch billing state so the banner
// disappears without waiting for the 60s polling tick.

describe('TrialBanner upgrade-now flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ canManageUsers: () => true });
    useBillingStatus.mockReturnValue(ACTIVE_TRIAL);
    billingAPI.upgradeNow.mockResolvedValue({});
  });

  it('opens confirm dialog when "Upgrade now" is clicked', async () => {
    const user = userEvent.setup();
    render(<TrialBanner />);

    await user.click(screen.getByRole('button', { name: 'Upgrade now' }));

    expect(screen.getByRole('dialog', { name: /upgrade now\?/i })).toBeInTheDocument();
    // The confirm dialog surfaces the projected cost so the customer
    // sees what they're agreeing to — pin this so the line doesn't get
    // accidentally dropped during a copy refactor.
    expect(screen.getByRole('dialog')).toHaveTextContent('$40.00/month');
  });

  it('fires upgradeNow mutation and refetches on confirm', async () => {
    const user = userEvent.setup();
    render(<TrialBanner />);

    await user.click(screen.getByRole('button', { name: 'Upgrade now' }));
    await user.click(screen.getByRole('button', { name: 'Confirm upgrade' }));

    await waitFor(() => {
      expect(billingAPI.upgradeNow).toHaveBeenCalledTimes(1);
    });
    expect(ACTIVE_TRIAL.refetch).toHaveBeenCalledTimes(1);
  });

  it('surfaces backend error message in-dialog on failure', async () => {
    // Pre-fix: failures only landed in console.error and the dialog closed,
    // leaving the admin unsure if the action took. The detail string from a
    // FastAPI HTTPException should reach the alert region inside the dialog.
    billingAPI.upgradeNow.mockRejectedValue({
      response: { data: { detail: 'no active trial' } },
    });
    const user = userEvent.setup();
    render(<TrialBanner />);

    await user.click(screen.getByRole('button', { name: 'Upgrade now' }));
    await user.click(screen.getByRole('button', { name: 'Confirm upgrade' }));

    await waitFor(() => {
      // Dialog stays open so the user can read the error and decide.
      expect(screen.getByRole('dialog', { name: /upgrade now\?/i })).toHaveTextContent('no active trial');
    });
    // Refetch must NOT fire on failure — state didn't change.
    expect(ACTIVE_TRIAL.refetch).not.toHaveBeenCalled();
  });
});

describe('TrialBanner cancel-trial flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ canManageUsers: () => true });
    useBillingStatus.mockReturnValue(ACTIVE_TRIAL);
    billingAPI.cancelTrial.mockResolvedValue({});
  });

  it('opens warning dialog when "Cancel trial" is clicked', async () => {
    const user = userEvent.setup();
    render(<TrialBanner />);

    await user.click(screen.getByRole('button', { name: 'Cancel trial' }));

    expect(screen.getByRole('dialog', { name: /cancel your trial\?/i })).toBeInTheDocument();
  });

  it('warning → typed dialog transition shows the CONFIRM input', async () => {
    const user = userEvent.setup();
    render(<TrialBanner />);

    await user.click(screen.getByRole('button', { name: 'Cancel trial' }));
    await user.click(screen.getByRole('button', { name: 'Continue to cancel' }));

    expect(screen.getByRole('dialog', { name: /confirm cancellation/i })).toBeInTheDocument();
    expect(screen.getByLabelText('Type CONFIRM to enable cancellation')).toBeInTheDocument();
  });

  it('submit button is disabled until "CONFIRM" is typed exactly', async () => {
    const user = userEvent.setup();
    render(<TrialBanner />);

    await user.click(screen.getByRole('button', { name: 'Cancel trial' }));
    await user.click(screen.getByRole('button', { name: 'Continue to cancel' }));

    const input = screen.getByLabelText('Type CONFIRM to enable cancellation');
    const submit = screen.getByRole('button', { name: 'Submit cancellation' });

    // Empty → disabled
    expect(submit).toBeDisabled();

    // Partial match → still disabled
    await user.type(input, 'CONF');
    expect(submit).toBeDisabled();

    // Case difference → still disabled (strict equality, not
    // case-insensitive — predictable gate for screen-reader users).
    await user.clear(input);
    await user.type(input, 'confirm');
    expect(submit).toBeDisabled();

    // Exact match → enabled
    await user.clear(input);
    await user.type(input, 'CONFIRM');
    expect(submit).toBeEnabled();
  });

  it('submit fires cancelTrial mutation and refetches on success', async () => {
    const user = userEvent.setup();
    render(<TrialBanner />);

    await user.click(screen.getByRole('button', { name: 'Cancel trial' }));
    await user.click(screen.getByRole('button', { name: 'Continue to cancel' }));
    await user.type(screen.getByLabelText('Type CONFIRM to enable cancellation'), 'CONFIRM');
    await user.click(screen.getByRole('button', { name: 'Submit cancellation' }));

    await waitFor(() => {
      expect(billingAPI.cancelTrial).toHaveBeenCalledTimes(1);
    });
    // No body sent — server-side token check was dropped per 16.2.
    // Asserting the call has no args pins that contract.
    expect(billingAPI.cancelTrial).toHaveBeenCalledWith();
    expect(ACTIVE_TRIAL.refetch).toHaveBeenCalledTimes(1);
  });

  it('surfaces backend error message in-dialog on failure', async () => {
    // Mirrors the upgrade flow: the cancel typed-confirm dialog should
    // render the backend's detail string inline rather than silently
    // closing or only logging to the console.
    billingAPI.cancelTrial.mockRejectedValue({
      response: { data: { detail: 'Billing provider unavailable' } },
    });
    const user = userEvent.setup();
    render(<TrialBanner />);

    await user.click(screen.getByRole('button', { name: 'Cancel trial' }));
    await user.click(screen.getByRole('button', { name: 'Continue to cancel' }));
    await user.type(screen.getByLabelText('Type CONFIRM to enable cancellation'), 'CONFIRM');
    await user.click(screen.getByRole('button', { name: 'Submit cancellation' }));

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: /confirm cancellation/i })).toHaveTextContent(
        'Billing provider unavailable'
      );
    });
    expect(ACTIVE_TRIAL.refetch).not.toHaveBeenCalled();
  });
});
