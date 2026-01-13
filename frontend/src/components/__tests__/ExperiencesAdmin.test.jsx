import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider } from '@mui/material/styles';
import { createTheme } from '@mui/material/styles';
import ExperiencesAdmin from '../ExperiencesAdmin';
import * as api from '../../services/api';

// Mock the API
jest.mock('../../services/api', () => ({
    experiencesAPI: {
        list: jest.fn(),
        delete: jest.fn(),
    },
    extractDataFromResponse: jest.fn(),
    formatError: jest.fn(),
}));

// Mock the ImportExperienceWizard component
jest.mock('../ImportExperienceWizard', () => {
    return function MockImportExperienceWizard({ open, onClose, onSuccess }) {
        return open ? (
            <div data-testid="import-wizard">
                <button onClick={onClose}>Close Wizard</button>
                <button onClick={() => onSuccess({ id: 'test-id', name: 'Test Experience' })}>
                    Success
                </button>
            </div>
        ) : null;
    };
});

// Mock other components
jest.mock('../ExperienceRunDialog', () => {
    return function MockExperienceRunDialog() {
        return <div data-testid="run-dialog" />;
    };
});

jest.mock('../ExportExperienceButton', () => {
    return function MockExportExperienceButton() {
        return <button data-testid="export-button">Export</button>;
    };
});

jest.mock('../PageHelpHeader', () => {
    return function MockPageHelpHeader() {
        return <div data-testid="page-help-header" />;
    };
});

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
                <ThemeProvider theme={theme}>
                    {component}
                </ThemeProvider>
            </BrowserRouter>
        </QueryClientProvider>
    );
};

describe('ExperiencesAdmin', () => {
    beforeEach(() => {
        // Mock successful API response
        api.experiencesAPI.list.mockResolvedValue({
            data: { items: [] }
        });
        api.extractDataFromResponse.mockReturnValue({ items: [] });
    });

    afterEach(() => {
        jest.clearAllMocks();
    });

    test('renders Import Experience button', async () => {
        renderWithProviders(<ExperiencesAdmin />);

        await waitFor(() => {
            expect(screen.getByText('Import Experience')).toBeInTheDocument();
        });
    });

    test('displays model configuration information in experience cards', async () => {
        // Mock API response with experience that has model configuration
        const mockExperiences = [
            {
                id: 'exp-1',
                name: 'Test Experience',
                description: 'Test description',
                visibility: 'published',
                trigger_type: 'manual',
                step_count: 2,
                model_configuration: {
                    id: 'config-1',
                    name: 'Research Assistant',
                    description: 'AI assistant for research tasks'
                }
            }
        ];

        api.experiencesAPI.list.mockResolvedValue({
            data: { items: mockExperiences }
        });
        api.extractDataFromResponse.mockReturnValue({ items: mockExperiences });

        renderWithProviders(<ExperiencesAdmin />);

        await waitFor(() => {
            expect(screen.getByText('Test Experience')).toBeInTheDocument();
        });

        // Check that model configuration name is displayed
        expect(screen.getByText('Research Assistant')).toBeInTheDocument();
        
        // Check that model configuration description is displayed
        expect(screen.getByText('AI assistant for research tasks')).toBeInTheDocument();
    });

    test('displays "No LLM synthesis configured" when no model configuration', async () => {
        // Mock API response with experience that has no model configuration
        const mockExperiences = [
            {
                id: 'exp-1',
                name: 'Test Experience',
                description: 'Test description',
                visibility: 'published',
                trigger_type: 'manual',
                step_count: 2,
                model_configuration: null
            }
        ];

        api.experiencesAPI.list.mockResolvedValue({
            data: { items: mockExperiences }
        });
        api.extractDataFromResponse.mockReturnValue({ items: mockExperiences });

        renderWithProviders(<ExperiencesAdmin />);

        await waitFor(() => {
            expect(screen.getByText('Test Experience')).toBeInTheDocument();
        });

        // Check that "No LLM synthesis configured" is displayed
        expect(screen.getByText('No LLM synthesis configured')).toBeInTheDocument();
    });

    test('opens import wizard when Import Experience button is clicked', async () => {
        renderWithProviders(<ExperiencesAdmin />);

        await waitFor(() => {
            expect(screen.getByText('Import Experience')).toBeInTheDocument();
        });

        // Click the Import Experience button
        fireEvent.click(screen.getByText('Import Experience'));

        // Verify the import wizard opens
        expect(screen.getByTestId('import-wizard')).toBeInTheDocument();
    });

    test('closes import wizard when close is called', async () => {
        renderWithProviders(<ExperiencesAdmin />);

        await waitFor(() => {
            expect(screen.getByText('Import Experience')).toBeInTheDocument();
        });

        // Open the wizard
        fireEvent.click(screen.getByText('Import Experience'));
        expect(screen.getByTestId('import-wizard')).toBeInTheDocument();

        // Close the wizard
        fireEvent.click(screen.getByText('Close Wizard'));
        expect(screen.queryByTestId('import-wizard')).not.toBeInTheDocument();
    });

    test('handles import success correctly', async () => {
        const mockNavigate = jest.fn();
        
        // Mock useNavigate
        jest.doMock('react-router-dom', () => ({
            ...jest.requireActual('react-router-dom'),
            useNavigate: () => mockNavigate,
        }));

        renderWithProviders(<ExperiencesAdmin />);

        await waitFor(() => {
            expect(screen.getByText('Import Experience')).toBeInTheDocument();
        });

        // Open the wizard
        fireEvent.click(screen.getByText('Import Experience'));
        expect(screen.getByTestId('import-wizard')).toBeInTheDocument();

        // Trigger success
        fireEvent.click(screen.getByText('Success'));

        // Verify wizard closes
        expect(screen.queryByTestId('import-wizard')).not.toBeInTheDocument();
    });

    test('Import Experience button has correct styling', async () => {
        renderWithProviders(<ExperiencesAdmin />);

        await waitFor(() => {
            expect(screen.getByText('Import Experience')).toBeInTheDocument();
        });

        const importButton = screen.getByText('Import Experience');
        expect(importButton).toHaveClass('MuiButton-outlined');
    });
});