import React from 'react';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import PlaceholderFormStep from '../PlaceholderFormStep';

// Mock the API calls
jest.mock('../../../services/api', () => ({
    modelConfigurationsAPI: {
        list: jest.fn(() => Promise.resolve({ data: { items: [] } })),
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

jest.mock('../../shared/ModelConfigurationSelector', () => {
    return function MockModelConfigurationSelector({ modelConfigurationId, onModelConfigurationChange, label, required }) {
        return (
            <div data-testid="model-configuration-selector">
                <label htmlFor="model-configuration">{label} {required ? '*' : ''}</label>
                <select 
                    id="model-configuration"
                    value={modelConfigurationId || ''} 
                    onChange={(e) => onModelConfigurationChange(e.target.value)}
                >
                    <option value="">None</option>
                    <option value="config-1">Research Assistant</option>
                    <option value="config-2">Customer Support</option>
                </select>
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

    test('handles YAML without model configuration placeholders gracefully', () => {
        // This test specifically addresses the bug where YAML without model_configuration_id
        // placeholder would cause the component to fail
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
        
        // Should NOT render model configuration selector since that placeholder isn't present
        expect(screen.queryByTestId('model-configuration-selector')).not.toBeInTheDocument();
    });

    test('renders model configuration selector when model_configuration_id placeholder is present', () => {
        render(
            <PlaceholderFormStep
                placeholders={['model_configuration_id']}
                values={{}}
                onValuesChange={mockOnValuesChange}
                onValidationChange={mockOnValidationChange}
            />,
            { wrapper: createWrapper() }
        );

        expect(screen.getByText('Configure Import Settings')).toBeInTheDocument();
        expect(screen.getByTestId('model-configuration-selector')).toBeInTheDocument();
        expect(screen.getByLabelText(/Model Configuration/)).toBeInTheDocument();
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