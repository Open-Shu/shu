import React from 'react';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import PlaceholderFormStep from '../PlaceholderFormStep';

// Mock the API calls
jest.mock('../../../services/api', () => ({
    llmAPI: {
        getProviders: jest.fn(() => Promise.resolve({ data: { items: [] } })),
        getModels: jest.fn(() => Promise.resolve({ data: { items: [] } })),
    },
    extractDataFromResponse: jest.fn((response) => response.data),
}));

// Mock the shared components to avoid complex dependencies
jest.mock('../../shared/TriggerConfiguration', () => {
    return function MockTriggerConfiguration({ triggerType, onTriggerTypeChange, validationErrors, required }) {
        return (
            <div data-testid="trigger-configuration">
                <label htmlFor="trigger-type">Trigger Type {required ? '*' : ''}</label>
                <select 
                    id="trigger-type"
                    value={triggerType} 
                    onChange={(e) => onTriggerTypeChange(e.target.value)}
                >
                    <option value="manual">Manual</option>
                    <option value="scheduled">Scheduled</option>
                    <option value="cron">Cron</option>
                </select>
                {validationErrors.trigger_type && (
                    <div>{validationErrors.trigger_type}</div>
                )}
            </div>
        );
    };
});

jest.mock('../../shared/LLMProviderSelector', () => {
    return function MockLLMProviderSelector({ providerId, onProviderChange, validationErrors, required, providerLabel }) {
        return (
            <div data-testid="llm-provider-selector">
                <label htmlFor="llm-provider">{providerLabel} {required ? '*' : ''}</label>
                <select 
                    id="llm-provider"
                    value={providerId} 
                    onChange={(e) => onProviderChange(e.target.value)}
                >
                    <option value="">None</option>
                    <option value="openai">OpenAI</option>
                </select>
                {validationErrors.llm_provider_id && (
                    <div>{validationErrors.llm_provider_id}</div>
                )}
            </div>
        );
    };
});

const createWrapper = () => {
    const queryClient = new QueryClient({
        defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
        },
    });
    
    return ({ children }) => (
        <QueryClientProvider client={queryClient}>
            {children}
        </QueryClientProvider>
    );
};

describe('PlaceholderFormStep', () => {
    const mockOnValuesChange = jest.fn();
    const mockOnValidationChange = jest.fn();

    beforeEach(() => {
        jest.clearAllMocks();
    });

    test('renders success message when no placeholders are provided', () => {
        render(
            <PlaceholderFormStep
                placeholders={[]}
                values={{}}
                onValuesChange={mockOnValuesChange}
                onValidationChange={mockOnValidationChange}
            />,
            { wrapper: createWrapper() }
        );

        expect(screen.getByText('Configuration Complete')).toBeInTheDocument();
        expect(screen.getByText(/No configuration placeholders found/)).toBeInTheDocument();
    });

    test('renders form fields for supported placeholders', () => {
        render(
            <PlaceholderFormStep
                placeholders={['max_run_seconds']}
                values={{}}
                onValuesChange={mockOnValuesChange}
                onValidationChange={mockOnValidationChange}
            />,
            { wrapper: createWrapper() }
        );

        expect(screen.getByText('Configure Import Settings')).toBeInTheDocument();
        expect(screen.getByLabelText(/Max Run Time/)).toBeInTheDocument();
    });

    test('handles YAML without LLM placeholders gracefully', () => {
        // This test specifically addresses the bug where YAML without llm_provider_id
        // and model_name placeholders would cause the component to fail
        render(
            <PlaceholderFormStep
                placeholders={['trigger_type', 'max_run_seconds']}
                values={{}}
                onValuesChange={mockOnValuesChange}
                onValidationChange={mockOnValidationChange}
            />,
            { wrapper: createWrapper() }
        );

        // Should render without crashing
        expect(screen.getByText('Configure Import Settings')).toBeInTheDocument();
        expect(screen.getByLabelText(/Trigger Type/)).toBeInTheDocument();
        expect(screen.getByLabelText(/Max Run Time/)).toBeInTheDocument();
        
        // Should NOT render LLM provider selector since those placeholders aren't present
        expect(screen.queryByTestId('llm-provider-selector')).not.toBeInTheDocument();
    });

    test('renders LLM provider selector only when llm_provider_id placeholder is present', () => {
        render(
            <PlaceholderFormStep
                placeholders={['llm_provider_id', 'model_name']}
                values={{}}
                onValuesChange={mockOnValuesChange}
                onValidationChange={mockOnValidationChange}
            />,
            { wrapper: createWrapper() }
        );

        expect(screen.getByText('Configure Import Settings')).toBeInTheDocument();
        expect(screen.getByTestId('llm-provider-selector')).toBeInTheDocument();
        expect(screen.getByLabelText(/LLM Provider/)).toBeInTheDocument();
    });

    test('shows warning for unsupported placeholders', () => {
        render(
            <PlaceholderFormStep
                placeholders={['max_run_seconds', 'unsupported_placeholder']}
                values={{}}
                onValuesChange={mockOnValuesChange}
                onValidationChange={mockOnValidationChange}
            />,
            { wrapper: createWrapper() }
        );

        expect(screen.getByText(/Some placeholders in your YAML are not configurable/)).toBeInTheDocument();
        expect(screen.getByText('unsupported_placeholder')).toBeInTheDocument();
    });
});