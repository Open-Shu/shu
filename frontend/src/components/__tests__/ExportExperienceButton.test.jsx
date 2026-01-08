import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from 'react-query';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ExportExperienceButton from '../ExportExperienceButton';
import { experiencesAPI } from '../../services/api';

// Mock the API
jest.mock('../../services/api', () => ({
    experiencesAPI: {
        export: jest.fn(),
    },
    formatError: jest.fn((error) => error.message || 'Unknown error'),
}));

// Mock URL.createObjectURL and revokeObjectURL
global.URL.createObjectURL = jest.fn(() => 'mock-blob-url');
global.URL.revokeObjectURL = jest.fn();

// Mock document.createElement and appendChild/removeChild
const mockLink = {
    href: '',
    download: '',
    click: jest.fn(),
};
const originalCreateElement = document.createElement;
document.createElement = jest.fn((tagName) => {
    if (tagName === 'a') {
        return mockLink;
    }
    return originalCreateElement.call(document, tagName);
});

const mockAppendChild = jest.fn();
const mockRemoveChild = jest.fn();
document.body.appendChild = mockAppendChild;
document.body.removeChild = mockRemoveChild;

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

    afterAll(() => {
        document.createElement = originalCreateElement;
    });

    it('renders icon button by default', () => {
        render(
            <ExportExperienceButton
                experienceId="test-id"
                experienceName="Test Experience"
            />,
            { wrapper: TestWrapper }
        );

        const button = screen.getByRole('button');
        expect(button).toBeInTheDocument();
    });

    it('renders button variant when specified', () => {
        render(
            <ExportExperienceButton
                experienceId="test-id"
                experienceName="Test Experience"
                variant="button"
            />,
            { wrapper: TestWrapper }
        );

        const button = screen.getByRole('button', { name: /export/i });
        expect(button).toBeInTheDocument();
        expect(button).toHaveTextContent('Export');
    });

    it('calls export API on click', async () => {
        const mockBlob = new Blob(['test yaml content'], { type: 'application/x-yaml' });
        experiencesAPI.export.mockResolvedValue({ data: mockBlob });

        render(
            <ExportExperienceButton
                experienceId="test-id"
                experienceName="Test Experience"
            />,
            { wrapper: TestWrapper }
        );

        const button = screen.getByRole('button');
        fireEvent.click(button);

        await waitFor(() => {
            expect(experiencesAPI.export).toHaveBeenCalledWith('test-id');
        });
    });

    it('is disabled when disabled prop is true', () => {
        render(
            <ExportExperienceButton
                experienceId="test-id"
                experienceName="Test Experience"
                disabled={true}
            />,
            { wrapper: TestWrapper }
        );

        const button = screen.getByRole('button');
        expect(button).toBeDisabled();
    });
});
