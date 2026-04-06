import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from 'react-query';
import ApiIntegrationDialog from '../ApiIntegrationDialog';

const mockCreateConnection = vi.fn(() =>
  Promise.resolve({ data: { data: { id: 'new-1', name: 'test-api' } } })
);

vi.mock('../../services/apiIntegrationsApi', () => ({
  apiIntegrationsAPI: {
    createConnection: (...args) => mockCreateConnection(...args),
  },
}));

vi.mock('../../services/api', () => ({
  extractDataFromResponse: (resp) => resp?.data?.data,
  formatError: (err) => err?.message || 'Unknown error',
}));

const renderWithProviders = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, cacheTime: 0 } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

describe('ApiIntegrationDialog', () => {
  const onClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders YAML input when open', () => {
    renderWithProviders(<ApiIntegrationDialog open={true} onClose={onClose} />);
    expect(screen.getByText('Add API Integration')).toBeInTheDocument();
    expect(screen.getByText('YAML Configuration')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/name: my-api/)).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    renderWithProviders(<ApiIntegrationDialog open={false} onClose={onClose} />);
    expect(screen.queryByText('Add API Integration')).not.toBeInTheDocument();
  });

  it('shows auth field when YAML has auth section', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ApiIntegrationDialog open={true} onClose={onClose} />);

    const yamlInput = screen.getByPlaceholderText(/name: my-api/);
    await user.clear(yamlInput);
    await user.type(yamlInput, 'auth:\n  type: bearer');

    await waitFor(() => {
      expect(screen.getByLabelText('Auth Credential')).toBeInTheDocument();
    });
  });

  it('does not show auth field when YAML has no auth section', () => {
    renderWithProviders(<ApiIntegrationDialog open={true} onClose={onClose} />);
    expect(screen.queryByLabelText('Auth Credential')).not.toBeInTheDocument();
  });

  it('shows error for empty YAML on submit', async () => {
    renderWithProviders(<ApiIntegrationDialog open={true} onClose={onClose} />);
    fireEvent.click(screen.getByText('Add Integration'));
    expect(screen.getByText('YAML content is required')).toBeInTheDocument();
  });

  it('shows error for invalid YAML on submit', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ApiIntegrationDialog open={true} onClose={onClose} />);

    const yamlInput = screen.getByPlaceholderText(/name: my-api/);
    await user.type(yamlInput, 'name: "unclosed');

    fireEvent.click(screen.getByText('Add Integration'));
    await waitFor(() => {
      expect(screen.getByText(/Invalid YAML/)).toBeInTheDocument();
    });
  });

  it('calls API on valid submit', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ApiIntegrationDialog open={true} onClose={onClose} />);

    const yamlInput = screen.getByPlaceholderText(/name: my-api/);
    await user.type(yamlInput, 'name: test-api');

    fireEvent.click(screen.getByText('Add Integration'));

    await waitFor(() => {
      expect(mockCreateConnection).toHaveBeenCalledWith('name: test-api', undefined);
    });
  });

  it('has cancel button that calls onClose', () => {
    renderWithProviders(<ApiIntegrationDialog open={true} onClose={onClose} />);
    fireEvent.click(screen.getByText('Cancel'));
    expect(onClose).toHaveBeenCalled();
  });
});
