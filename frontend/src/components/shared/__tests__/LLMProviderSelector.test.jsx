import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import LLMProviderSelector from '../LLMProviderSelector';
import * as api from '../../../services/api';

// Mock the API
jest.mock('../../../services/api');

const createTestQueryClient = () => new QueryClient({
    defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
    },
});

const renderWithQueryClient = (component) => {
    const queryClient = createTestQueryClient();
    return render(
        <QueryClientProvider client={queryClient}>
            {component}
        </QueryClientProvider>
    );
};

describe('LLMProviderSelector', () => {
    const defaultProps = {
        providerId: '',
        modelName: '',
        onProviderChange: jest.fn(),
        onModelChange: jest.fn(),
        validationErrors: {},
        required: false,
        showHelperText: true,
        providerLabel: 'LLM Provider',
        modelLabel: 'Model',
    };

    const mockProviders = [
        { id: 'openai', name: 'OpenAI' },
        { id: 'anthropic', name: 'Anthropic' },
    ];

    const mockModels = [
        { provider_id: 'openai', model_name: 'gpt-4', is_active: true },
        { provider_id: 'openai', model_name: 'gpt-3.5-turbo', is_active: true },
        { provider_id: 'anthropic', model_name: 'claude-3', is_active: true },
    ];

    beforeEach(() => {
        jest.clearAllMocks();
        api.llmAPI.getProviders.mockResolvedValue({ data: mockProviders });
        api.llmAPI.getModels.mockResolvedValue({ data: mockModels });
        api.extractDataFromResponse.mockImplementation((response) => response.data);
    });

    test('renders provider selector', async () => {
        renderWithQueryClient(<LLMProviderSelector {...defaultProps} />);
        
        await waitFor(() => {
            expect(screen.getByRole('combobox')).toBeInTheDocument();
        });
    });

    test('shows model selector when provider is selected', async () => {
        renderWithQueryClient(
            <LLMProviderSelector 
                {...defaultProps} 
                providerId="openai"
                modelName="gpt-4"
            />
        );
        
        await waitFor(() => {
            const comboboxes = screen.getAllByRole('combobox');
            expect(comboboxes).toHaveLength(2); // Provider and Model selectors
        });
    });

    test('shows info alert when no provider is selected', async () => {
        renderWithQueryClient(<LLMProviderSelector {...defaultProps} />);
        
        await waitFor(() => {
            expect(screen.getByText('Select an LLM provider first to choose a model.')).toBeInTheDocument();
        });
    });

    test('calls onProviderChange when provider changes', async () => {
        renderWithQueryClient(<LLMProviderSelector {...defaultProps} />);
        
        await waitFor(() => {
            expect(screen.getByRole('combobox')).toBeInTheDocument();
        });

        const select = screen.getByRole('combobox');
        fireEvent.mouseDown(select);
        
        await waitFor(() => {
            const openaiOption = screen.getByText('OpenAI');
            fireEvent.click(openaiOption);
        });
        
        expect(defaultProps.onProviderChange).toHaveBeenCalledWith('openai');
    });

    test('calls onModelChange when model changes', async () => {
        renderWithQueryClient(
            <LLMProviderSelector 
                {...defaultProps} 
                providerId="openai"
                modelName=""
            />
        );
        
        await waitFor(() => {
            const comboboxes = screen.getAllByRole('combobox');
            expect(comboboxes).toHaveLength(2);
        });

        const comboboxes = screen.getAllByRole('combobox');
        const modelSelect = comboboxes[1]; // Second combobox is the model selector
        fireEvent.mouseDown(modelSelect);
        
        await waitFor(() => {
            const gpt4Option = screen.getByText('gpt-4');
            fireEvent.click(gpt4Option);
        });
        
        expect(defaultProps.onModelChange).toHaveBeenCalledWith('gpt-4');
    });

    test('displays validation errors', async () => {
        renderWithQueryClient(
            <LLMProviderSelector 
                {...defaultProps} 
                validationErrors={{ llm_provider_id: 'Provider is required' }}
            />
        );
        
        await waitFor(() => {
            expect(screen.getByText('Provider is required')).toBeInTheDocument();
        });
    });

    test('shows required indicator when required is true', async () => {
        renderWithQueryClient(<LLMProviderSelector {...defaultProps} required={true} />);
        
        await waitFor(() => {
            expect(screen.getAllByText(/LLM Provider \*/)).toHaveLength(2); // Label and legend
        });
    });

    test('filters models by selected provider', async () => {
        renderWithQueryClient(
            <LLMProviderSelector 
                {...defaultProps} 
                providerId="openai"
                modelName=""
            />
        );
        
        await waitFor(() => {
            const comboboxes = screen.getAllByRole('combobox');
            expect(comboboxes).toHaveLength(2);
        });

        const comboboxes = screen.getAllByRole('combobox');
        const modelSelect = comboboxes[1]; // Second combobox is the model selector
        fireEvent.mouseDown(modelSelect);
        
        await waitFor(() => {
            // Should show OpenAI models
            expect(screen.getByText('gpt-4')).toBeInTheDocument();
            expect(screen.getByText('gpt-3.5-turbo')).toBeInTheDocument();
            // Should not show Anthropic models
            expect(screen.queryByText('claude-3')).not.toBeInTheDocument();
        });
    });
});