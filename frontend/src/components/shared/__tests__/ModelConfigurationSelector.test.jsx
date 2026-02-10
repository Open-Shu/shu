import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { vi } from 'vitest';
import ModelConfigurationSelector from '../ModelConfigurationSelector';
import * as api from '../../../services/api';

// Mock the API
vi.mock('../../../services/api', () => ({
  modelConfigAPI: {
    list: vi.fn(),
  },
  extractDataFromResponse: vi.fn(),
}));

const mockModelConfigurations = [
  {
    id: 'config-1',
    name: 'Research Assistant',
    description: 'Configuration for research tasks',
    llm_provider: { name: 'OpenAI' },
    model_name: 'gpt-4-turbo',
    prompt: { name: 'Research Prompt' },
    parameter_overrides: { temperature: 0.7 },
    is_active: true,
  },
  {
    id: 'config-2',
    name: 'Customer Support',
    description: 'Configuration for customer support',
    llm_provider: { name: 'Anthropic' },
    model_name: 'claude-3-sonnet',
    prompt: null,
    parameter_overrides: {},
    is_active: true,
  },
];

const renderWithQueryClient = (component) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={queryClient}>{component}</QueryClientProvider>);
};

describe('ModelConfigurationSelector', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Mock the API to return the expected data structure
    api.modelConfigAPI.list.mockResolvedValue({
      data: {
        items: mockModelConfigurations,
      },
    });
    api.extractDataFromResponse.mockReturnValue({
      items: mockModelConfigurations,
    });
  });

  it('renders the selector with label', async () => {
    renderWithQueryClient(
      <ModelConfigurationSelector
        modelConfigurationId=""
        onModelConfigurationChange={jest.fn()}
        label="Test Configuration"
      />
    );

    expect(screen.getByLabelText(/Test Configuration/)).toBeInTheDocument();
  });

  it('loads and displays model configurations', async () => {
    renderWithQueryClient(
      <ModelConfigurationSelector modelConfigurationId="" onModelConfigurationChange={jest.fn()} />
    );

    // Wait for the API call to complete
    await waitFor(() => {
      expect(api.modelConfigAPI.list).toHaveBeenCalledWith({
        is_active: true,
        include_relationships: true,
      });
    });

    // Wait for the component to not be in loading state
    await waitFor(() => {
      expect(screen.queryByText(/Loading model configurations/)).not.toBeInTheDocument();
    });

    // Click to open the dropdown
    fireEvent.mouseDown(screen.getByRole('combobox'));

    await waitFor(() => {
      expect(screen.getByText('Research Assistant')).toBeInTheDocument();
      expect(screen.getByText('Customer Support')).toBeInTheDocument();
      expect(screen.getByText('No LLM Synthesis')).toBeInTheDocument();
    });
  });

  it('calls onModelConfigurationChange when selection changes', async () => {
    const mockOnChange = jest.fn();

    renderWithQueryClient(
      <ModelConfigurationSelector modelConfigurationId="" onModelConfigurationChange={mockOnChange} />
    );

    // Wait for the API call to complete
    await waitFor(() => {
      expect(api.modelConfigAPI.list).toHaveBeenCalled();
    });

    // Wait for the component to not be in loading state
    await waitFor(() => {
      expect(screen.queryByText(/Loading model configurations/)).not.toBeInTheDocument();
    });

    // Click to open the dropdown
    fireEvent.mouseDown(screen.getByRole('combobox'));

    await waitFor(() => {
      expect(screen.getByText('Research Assistant')).toBeInTheDocument();
    });

    // Select an option
    fireEvent.click(screen.getByText('Research Assistant'));

    expect(mockOnChange).toHaveBeenCalledWith('config-1');
  });

  it('shows configuration details when selected', async () => {
    renderWithQueryClient(
      <ModelConfigurationSelector
        modelConfigurationId="config-1"
        onModelConfigurationChange={jest.fn()}
        showDetails={true}
      />
    );

    await waitFor(() => {
      expect(api.modelConfigAPI.list).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(screen.getByText('Selected Configuration')).toBeInTheDocument();
      expect(screen.getByText('Configuration for research tasks')).toBeInTheDocument();
      expect(screen.getAllByText('OpenAI - gpt-4-turbo')).toHaveLength(2); // One in select, one in chip
      expect(screen.getByText('Research Prompt')).toBeInTheDocument();
    });
  });

  it('shows validation error when present', () => {
    const validationErrors = {
      model_configuration_id: 'Model configuration is required',
    };

    renderWithQueryClient(
      <ModelConfigurationSelector
        modelConfigurationId=""
        onModelConfigurationChange={jest.fn()}
        validationErrors={validationErrors}
      />
    );

    expect(screen.getByText('Model configuration is required')).toBeInTheDocument();
  });

  it('shows empty state when no configurations available', async () => {
    api.modelConfigAPI.list.mockResolvedValue({
      data: {
        items: [],
      },
    });
    api.extractDataFromResponse.mockReturnValue({
      items: [],
    });

    renderWithQueryClient(
      <ModelConfigurationSelector
        modelConfigurationId=""
        onModelConfigurationChange={jest.fn()}
        showHelperText={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/No active model configurations found/)).toBeInTheDocument();
    });
  });

  it('handles API errors gracefully', async () => {
    api.modelConfigAPI.list.mockRejectedValue(new Error('API Error'));

    renderWithQueryClient(
      <ModelConfigurationSelector modelConfigurationId="" onModelConfigurationChange={jest.fn()} />
    );

    await waitFor(() => {
      expect(screen.getByText(/Failed to load model configurations/)).toBeInTheDocument();
    });
  });
});
