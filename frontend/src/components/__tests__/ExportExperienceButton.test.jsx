/**
 * @jest-environment jsdom
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from 'react-query';
import { ThemeProvider, createTheme } from '@mui/material/styles';

// Mock the API and utils BEFORE importing the component
jest.mock('../../services/api', () => ({
    experiencesAPI: {
        export: jest.fn(),
    },
    formatError: jest.fn((error) => error.message || 'Unknown error'),
}));

jest.mock('../../utils/downloadHelpers', () => ({
    downloadResponseAsFile: jest.fn(),
    generateSafeFilename: jest.fn((name) => name.toLowerCase().replace(/\s+/g, '-')),
}));

jest.mock('../../utils/log', () => ({
    log: {
        info: jest.fn(),
        error: jest.fn(),
    },
}));

// Import the component AFTER mocking its dependencies
import ExportExperienceButton from '../ExportExperienceButton';
import { experiencesAPI } from '../../services/api';

const createTestQueryClient = () => new QueryClient({
    defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
    },
});

// Test wrapper component
const TestWrapper = ({ children }) => {
    const queryClient = createTestQueryClient();
    const theme = createTheme();
    return (
        <QueryClientProvider client={queryClient}>
            <ThemeProvider theme={theme}>
                {children}
            </ThemeProvider>
        </QueryClientProvider>
    );
};

describe('ExportExperienceButton', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('renders icon button by default', () => {
        render(
            <TestWrapper>
                <ExportExperienceButton
                    experienceId="test-id"
                    experienceName="Test Experience"
                />
            </TestWrapper>
        );

        const button = screen.getByRole('button');
        expect(button).toBeInTheDocument();
    });

    it('renders button variant when specified', () => {
        render(
            <TestWrapper>
                <ExportExperienceButton
                    experienceId="test-id"
                    experienceName="Test Experience"
                    variant="button"
                />
            </TestWrapper>
        );

        const button = screen.getByRole('button', { name: /export/i });
        expect(button).toBeInTheDocument();
        expect(button).toHaveTextContent('Export');
    });

    it('calls export API on click', async () => {
        const mockBlob = new Blob(['test yaml content'], { type: 'application/x-yaml' });
        experiencesAPI.export.mockResolvedValue({ data: mockBlob });

        render(
            <TestWrapper>
                <ExportExperienceButton
                    experienceId="test-id"
                    experienceName="Test Experience"
                />
            </TestWrapper>
        );

        const button = screen.getByRole('button');
        fireEvent.click(button);

        await waitFor(() => {
            expect(experiencesAPI.export).toHaveBeenCalledWith('test-id');
        });
    });

    it('is disabled when disabled prop is true', () => {
        render(
            <TestWrapper>
                <ExportExperienceButton
                    experienceId="test-id"
                    experienceName="Test Experience"
                    disabled={true}
                />
            </TestWrapper>
        );

        const button = screen.getByRole('button');
        expect(button).toBeDisabled();
    });
});
