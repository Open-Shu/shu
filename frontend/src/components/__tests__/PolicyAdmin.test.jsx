import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { vi } from 'vitest';
import PolicyAdmin from '../PolicyAdmin';
import * as api from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    policyAPI: {
      ...actual.policyAPI,
      list: vi.fn(),
      get: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
      actions: vi.fn().mockResolvedValue({ data: { data: [] } }),
    },
    authAPI: { ...actual.authAPI, getUsers: vi.fn().mockResolvedValue({ data: { data: [] } }) },
    groupsAPI: { ...actual.groupsAPI, list: vi.fn().mockResolvedValue({ data: { data: [] } }) },
    experiencesAPI: { ...actual.experiencesAPI, list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    extractItemsFromResponse: vi.fn(),
    extractDataFromResponse: vi.fn(),
    extractPaginationFromResponse: vi.fn(),
    formatError: vi.fn((err) => err?.message || 'Something went wrong'),
  };
});

vi.mock('../../services/pluginsApi', () => ({
  pluginsAPI: { list: vi.fn().mockResolvedValue({ data: { data: [] } }) },
}));

vi.mock('../PageHelpHeader', () => ({
  default: ({ actions }) => <div data-testid="page-help-header">{actions}</div>,
}));

const theme = createTheme();

const renderWithProviders = (component) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ThemeProvider theme={theme}>{component}</ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
};

const MOCK_POLICY = {
  id: 'policy-1',
  name: 'allow-engineering',
  description: 'Allow engineering group access',
  effect: 'allow',
  is_active: true,
  bindings: [{ actor_type: 'group', actor_id: 'group-eng' }],
  statements: [{ actions: ['experience.read'], resources: ['experience:*'] }],
  created_at: '2025-01-15T00:00:00Z',
};

const MOCK_DENY_POLICY = {
  id: 'policy-2',
  name: 'deny-hr-pulse',
  description: 'Deny HR from project pulse',
  effect: 'deny',
  is_active: false,
  bindings: [],
  statements: [{ actions: ['experience.*'], resources: ['experience:project-pulse'] }],
  created_at: '2025-02-01T00:00:00Z',
};

describe('PolicyAdmin', () => {
  beforeEach(() => {
    api.policyAPI.list.mockResolvedValue({ data: { items: [], total: 0 } });
    api.extractItemsFromResponse.mockReturnValue([]);
    api.extractPaginationFromResponse.mockReturnValue({ total: 0 });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  test('shows loading spinner initially', () => {
    api.policyAPI.list.mockReturnValue(new Promise(() => {}));
    renderWithProviders(<PolicyAdmin />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  test('renders empty state when no policies exist', async () => {
    renderWithProviders(<PolicyAdmin />);
    await waitFor(() => {
      expect(screen.getByText(/no access policies found/i)).toBeInTheDocument();
    });
  });

  test('renders policy table with data', async () => {
    api.extractItemsFromResponse.mockReturnValue([MOCK_POLICY, MOCK_DENY_POLICY]);
    api.extractPaginationFromResponse.mockReturnValue({ total: 2 });
    renderWithProviders(<PolicyAdmin />);

    await waitFor(() => {
      expect(screen.getByText('allow-engineering')).toBeInTheDocument();
    });
    expect(screen.getByText('deny-hr-pulse')).toBeInTheDocument();

    // Effect chips
    expect(screen.getByText('allow')).toBeInTheDocument();
    expect(screen.getByText('deny')).toBeInTheDocument();

    // Active/Inactive chips (multiple "Active" nodes possible from effect + status)
    expect(screen.getAllByText('Active').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Inactive')).toBeInTheDocument();
  });

  test('passes pagination params to API', async () => {
    renderWithProviders(<PolicyAdmin />);
    await waitFor(() => {
      expect(api.policyAPI.list).toHaveBeenCalledWith({ offset: 0, limit: 10 });
    });
  });

  test('opens create dialog with template JSON', async () => {
    renderWithProviders(<PolicyAdmin />);
    await waitFor(() => {
      expect(screen.getByText('Create Policy')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /create policy/i }));

    // Switch to JSON mode to inspect the serialized template
    fireEvent.click(screen.getByRole('button', { name: /json editor/i }));

    const textField = screen.getByRole('textbox');
    expect(textField.value).toContain('"effect"');
    expect(textField.value).toContain('"is_active"');
  });

  test('opens edit dialog with existing policy JSON', async () => {
    api.extractItemsFromResponse.mockReturnValue([MOCK_POLICY]);
    api.extractPaginationFromResponse.mockReturnValue({ total: 1 });
    renderWithProviders(<PolicyAdmin />);

    await waitFor(() => {
      expect(screen.getByText('allow-engineering')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /edit policy/i }));

    await waitFor(() => {
      expect(screen.getByText('Edit Policy')).toBeInTheDocument();
    });

    // Switch to JSON mode to inspect the serialized policy
    fireEvent.click(screen.getByRole('button', { name: /json editor/i }));

    const textField = screen.getByRole('textbox');
    expect(textField.value).toContain('allow-engineering');
  });

  test('shows JSON validation error for invalid input', async () => {
    renderWithProviders(<PolicyAdmin />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /create policy/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /create policy/i }));

    // Switch to JSON mode and enter invalid JSON
    fireEvent.click(screen.getByRole('button', { name: /json editor/i }));

    const textField = screen.getByRole('textbox');
    fireEvent.change(textField, { target: { value: '{not valid json' } });

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(screen.getByText(/invalid json/i)).toBeInTheDocument();
  });

  test('calls create mutation with parsed JSON', async () => {
    api.policyAPI.create.mockResolvedValue({ data: MOCK_POLICY });
    renderWithProviders(<PolicyAdmin />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /create policy/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /create policy/i }));

    // Switch to JSON mode and enter policy JSON
    fireEvent.click(screen.getByRole('button', { name: /json editor/i }));

    const policyJson = JSON.stringify({ name: 'new-policy', effect: 'allow' });
    const textField = screen.getByRole('textbox');
    fireEvent.change(textField, { target: { value: policyJson } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(api.policyAPI.create).toHaveBeenCalledWith({
        name: 'new-policy',
        description: '',
        effect: 'allow',
        is_active: true,
        bindings: [],
        statements: [],
      });
    });
  });

  test('opens and confirms delete dialog', async () => {
    api.extractItemsFromResponse.mockReturnValue([MOCK_POLICY]);
    api.extractPaginationFromResponse.mockReturnValue({ total: 1 });
    api.policyAPI.delete.mockResolvedValue({});
    renderWithProviders(<PolicyAdmin />);

    await waitFor(() => {
      expect(screen.getByText('allow-engineering')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /delete policy/i }));

    await waitFor(() => {
      expect(screen.getByText(/are you sure you want to delete/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/"allow-engineering"/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^delete$/i }));

    await waitFor(() => {
      expect(api.policyAPI.delete).toHaveBeenCalledWith('policy-1');
    });
  });

  test('displays API error in alert', async () => {
    api.policyAPI.list.mockRejectedValue(new Error('Network error'));
    api.formatError.mockReturnValue('Network error');
    renderWithProviders(<PolicyAdmin />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument();
    });
  });
});
