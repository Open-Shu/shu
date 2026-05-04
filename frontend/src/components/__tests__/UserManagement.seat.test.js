import { render, screen, within, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from 'react-query';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { vi } from 'vitest';
import UserManagement from '../UserManagement';
import * as api from '../../services/api';
import { useAuth } from '../../hooks/useAuth';

vi.mock('../../services/api', () => ({
  authAPI: {
    getUsers: vi.fn(),
    createUser: vi.fn(),
    updateUser: vi.fn(),
    deleteUser: vi.fn(),
    activateUser: vi.fn(),
    deactivateUser: vi.fn(),
    scheduleUserDeactivation: vi.fn(),
    unscheduleUserDeactivation: vi.fn(),
  },
  billingAPI: {
    getSubscription: vi.fn(),
    releaseSeat: vi.fn(),
    cancelPendingRelease: vi.fn(),
  },
  extractDataFromResponse: vi.fn((response) => response?.data),
  formatError: vi.fn((err) => ({ message: err?.message || 'error' })),
}));

vi.mock('../../hooks/useAuth', () => ({
  useAuth: vi.fn(),
}));

vi.mock('../../utils/userHelpers', () => ({
  resolveUserId: (u) => u?.user_id || u?.id,
}));

vi.mock('../PageHelpHeader', () => ({
  default: ({ actions }) => <div data-testid="header-actions">{actions}</div>,
}));

vi.mock('../ResetPasswordDialog', () => ({ default: () => null }));
vi.mock('../EffectivePermissionsDialog', () => ({ default: () => null }));

const makeUser = (overrides = {}) => ({
  user_id: 1,
  email: 'alice@example.com',
  name: 'Alice',
  role: 'regular_user',
  is_active: true,
  auth_method: 'password',
  deactivation_scheduled_at: null,
  last_login: null,
  ...overrides,
});

const makeSubscription = (overrides = {}) => ({
  user_count: 2,
  user_limit: 3,
  user_limit_enforcement: 'hard',
  at_user_limit: false,
  current_period_end: '2026-05-01T00:00:00+00:00',
  ...overrides,
});

const renderComponent = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, cacheTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={createTheme()}>
        <UserManagement />
      </ThemeProvider>
    </QueryClientProvider>
  );
};

const setupMocks = ({ users, subscription } = {}) => {
  useAuth.mockReturnValue({ canManageUsers: () => true });
  api.authAPI.getUsers.mockResolvedValue({ data: users });
  api.billingAPI.getSubscription.mockResolvedValue({ data: subscription });
};

describe('UserManagement — inline seat affordances (SHU-730)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders no seat UI when user_limit_enforcement is not "hard"', async () => {
    setupMocks({
      users: [makeUser()],
      subscription: makeSubscription({ user_limit_enforcement: 'none' }),
    });
    renderComponent();

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    expect(screen.queryByRole('button', { name: /Schedule deactivation/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Release one open seat/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/open seat/i)).not.toBeInTheDocument();
  });

  it('renders flag button on each active row when enforcement is hard', async () => {
    setupMocks({
      users: [makeUser({ user_id: 1 }), makeUser({ user_id: 2, email: 'bob@x.com', name: 'Bob' })],
      subscription: makeSubscription(),
    });
    renderComponent();

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    const flagButtons = screen.getAllByRole('button', {
      name: /Schedule deactivation on period end/i,
    });
    expect(flagButtons).toHaveLength(2);
  });

  it('renders unflag button (not flag) on rows with deactivation_scheduled_at set', async () => {
    setupMocks({
      users: [makeUser({ deactivation_scheduled_at: '2026-04-20T00:00:00+00:00' })],
      subscription: makeSubscription(),
    });
    renderComponent();

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    expect(screen.getByRole('button', { name: /Cancel scheduled deactivation/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Schedule deactivation on period end/i })).not.toBeInTheDocument();
  });

  it('renders "Loses access on …" countdown on flagged rows', async () => {
    setupMocks({
      users: [makeUser({ deactivation_scheduled_at: '2026-04-20T00:00:00+00:00' })],
      subscription: makeSubscription({ current_period_end: '2026-05-01T00:00:00+00:00' }),
    });
    renderComponent();

    const expected = new Date('2026-05-01T00:00:00+00:00').toLocaleDateString();
    await waitFor(() => {
      expect(screen.getByText(`Loses access on ${expected}`)).toBeInTheDocument();
    });
  });

  it('shows open-seat count derived from active users vs quantity', async () => {
    setupMocks({
      users: [makeUser({ user_id: 1 }), makeUser({ user_id: 2, email: 'b@x.c', name: 'B' })],
      subscription: makeSubscription({ user_limit: 5 }),
    });
    renderComponent();

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    // 5 quantity - 2 active = 3 open seats
    const actions = screen.getByTestId('header-actions');
    expect(within(actions).getByText(/3 open seats/i)).toBeInTheDocument();
  });

  it('disables Release 1 seat button when open_seats === 0', async () => {
    setupMocks({
      users: [
        makeUser({ user_id: 1 }),
        makeUser({ user_id: 2, email: 'b@x.c', name: 'B' }),
        makeUser({ user_id: 3, email: 'c@x.c', name: 'C' }),
      ],
      subscription: makeSubscription({ user_limit: 3 }),
    });
    renderComponent();

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    const actions = screen.getByTestId('header-actions');
    const releaseBtn = within(actions).getByRole('button', { name: /Release one open seat/i });
    expect(releaseBtn).toBeDisabled();
  });

  it('enables Release 1 seat button when open_seats > 0', async () => {
    setupMocks({
      users: [makeUser({ user_id: 1 })],
      subscription: makeSubscription({ user_limit: 3 }),
    });
    renderComponent();

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    const actions = screen.getByTestId('header-actions');
    const releaseBtn = within(actions).getByRole('button', { name: /Release one open seat/i });
    expect(releaseBtn).toBeEnabled();
  });

  it('shows Cancel all pending reductions button when target_quantity < user_limit', async () => {
    setupMocks({
      users: [makeUser({ user_id: 1 })],
      subscription: makeSubscription({ user_limit: 5, target_quantity: 3 }),
    });
    renderComponent();

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    expect(
      screen.getByRole('button', { name: /Cancel all pending seat reductions and unflag scheduled users/i })
    ).toBeInTheDocument();
  });

  it('hides Cancel all pending reductions button when target_quantity == user_limit', async () => {
    setupMocks({
      users: [makeUser({ user_id: 1 })],
      subscription: makeSubscription({ user_limit: 4, target_quantity: 4 }),
    });
    renderComponent();

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    expect(
      screen.queryByRole('button', { name: /Cancel all pending seat reductions and unflag scheduled users/i })
    ).not.toBeInTheDocument();
  });

  it('disables Release 1 seat when target_quantity equals active count (no headroom)', async () => {
    // Pending downgrade already brought target down to current active count.
    // Releasing further would force a random trim at rollover; admin must
    // flag a specific user to schedule a reduction.
    setupMocks({
      users: [makeUser({ user_id: 1 }), makeUser({ user_id: 2, email: 'b@x.c', name: 'B' })],
      subscription: makeSubscription({ user_limit: 4, target_quantity: 2 }),
    });
    renderComponent();

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    const actions = screen.getByTestId('header-actions');
    const releaseBtn = within(actions).getByRole('button', { name: /Release one open seat/i });
    expect(releaseBtn).toBeDisabled();
  });
});
