import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Mock the CSS import
jest.mock('react-js-cron/dist/styles.css', () => ({}));

// Mock the cron library to avoid CSS import issues
jest.mock('react-js-cron', () => ({
    Cron: ({ value, setValue }) => (
        <div data-testid="cron-component">
            <input
                data-testid="mock-cron-input"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="Cron Expression"
            />
        </div>
    ),
}));

// Mock the schedulePreview utility
jest.mock('../../../utils/schedulePreview', () => ({
    getSchedulePreview: jest.fn(),
}));

import RecurringScheduleBuilder from '../RecurringScheduleBuilder';
import { getSchedulePreview } from '../../../utils/schedulePreview';

describe('RecurringScheduleBuilder', () => {
    const defaultProps = {
        value: { cron: '0 9 * * *', timezone: 'America/New_York' },
        onChange: jest.fn(),
        validationErrors: {},
    };

    beforeEach(() => {
        jest.clearAllMocks();
    });

    test('renders schedule configuration', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        expect(screen.getByText('Schedule Configuration')).toBeInTheDocument();
        expect(screen.getAllByText('Timezone').length).toBeGreaterThanOrEqual(2); // Header, label, legend, and chip
    });

    test('displays current cron expression', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        expect(screen.getByTestId('mock-cron-input')).toHaveValue('0 9 * * *');
    });

    test('displays current timezone', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        const timezoneInput = screen.getByRole('combobox');
        expect(timezoneInput).toBeInTheDocument();
    });

    test('calls onChange when cron expression changes', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        const cronInput = screen.getByTestId('mock-cron-input');
        fireEvent.change(cronInput, { target: { value: '0 10 * * *' } });
        
        expect(defaultProps.onChange).toHaveBeenCalledWith({
            cron: '0 10 * * *',
            timezone: 'America/New_York',
        });
    });

    test('handles empty values', () => {
        render(
            <RecurringScheduleBuilder 
                value={{}}
                onChange={jest.fn()}
                validationErrors={{}}
            />
        );
        
        expect(screen.getByTestId('mock-cron-input')).toHaveValue('0 9 * * *'); // default value
    });

    test('displays validation errors', () => {
        render(
            <RecurringScheduleBuilder 
                {...defaultProps}
                validationErrors={{ cron: 'Invalid cron expression' }}
            />
        );
        
        expect(screen.getByText('Invalid cron expression')).toBeInTheDocument();
    });

    test('displays schedule preview section', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        expect(screen.getByText('Schedule Preview')).toBeInTheDocument();
    });

    test('displays schedule preview when cron and timezone are set', async () => {
        const mockPreview = {
            description: 'At 09:00 AM (EST)',
            nextExecutions: [
                'Tuesday, January 14, 2026 at 9:00 AM EST',
                'Wednesday, January 15, 2026 at 9:00 AM EST',
                'Thursday, January 16, 2026 at 9:00 AM EST',
            ],
            executionDates: [],
        };

        getSchedulePreview.mockReturnValue(mockPreview);

        render(<RecurringScheduleBuilder {...defaultProps} />);

        // Wait for the debounced preview to load
        await waitFor(() => {
            expect(screen.getByText('At 09:00 AM (EST)')).toBeInTheDocument();
        }, { timeout: 500 });

        expect(screen.getByText('Tuesday, January 14, 2026 at 9:00 AM EST')).toBeInTheDocument();
        expect(screen.getByText('Wednesday, January 15, 2026 at 9:00 AM EST')).toBeInTheDocument();
        expect(screen.getByText('Thursday, January 16, 2026 at 9:00 AM EST')).toBeInTheDocument();
    });

    test('displays error message when preview generation fails', async () => {
        getSchedulePreview.mockImplementation(() => {
            throw new Error('Invalid cron expression format');
        });

        render(<RecurringScheduleBuilder {...defaultProps} />);

        // Wait for the debounced preview to attempt loading
        await waitFor(() => {
            expect(screen.getByText('Invalid cron expression format')).toBeInTheDocument();
        }, { timeout: 500 });
    });

    test('updates preview in real-time when cron changes', async () => {
        const mockPreview1 = {
            description: 'At 09:00 AM (EST)',
            nextExecutions: ['Tuesday, January 14, 2026 at 9:00 AM EST'],
            executionDates: [],
        };

        const mockPreview2 = {
            description: 'At 10:00 AM (EST)',
            nextExecutions: ['Tuesday, January 14, 2026 at 10:00 AM EST'],
            executionDates: [],
        };

        getSchedulePreview.mockReturnValueOnce(mockPreview1);

        const { rerender } = render(<RecurringScheduleBuilder {...defaultProps} />);

        // Wait for initial preview
        await waitFor(() => {
            expect(screen.getByText('At 09:00 AM (EST)')).toBeInTheDocument();
        }, { timeout: 500 });

        // Update cron expression
        getSchedulePreview.mockReturnValueOnce(mockPreview2);
        rerender(
            <RecurringScheduleBuilder 
                {...defaultProps}
                value={{ cron: '0 10 * * *', timezone: 'America/New_York' }}
            />
        );

        // Wait for updated preview
        await waitFor(() => {
            expect(screen.getByText('At 10:00 AM (EST)')).toBeInTheDocument();
        }, { timeout: 500 });
    });

    test('shows loading state while generating preview', async () => {
        getSchedulePreview.mockImplementation(() => {
            return new Promise(resolve => setTimeout(() => resolve({
                description: 'At 09:00 AM (EST)',
                nextExecutions: [],
                executionDates: [],
            }), 100));
        });

        render(<RecurringScheduleBuilder {...defaultProps} />);

        // Should show loading state initially
        expect(screen.getByText('Calculating next execution times...')).toBeInTheDocument();

        // Wait for preview to load
        await waitFor(() => {
            expect(screen.queryByText('Calculating next execution times...')).not.toBeInTheDocument();
        }, { timeout: 500 });
    });

    test('handles missing timezone gracefully', () => {
        render(
            <RecurringScheduleBuilder 
                value={{ cron: '0 9 * * *', timezone: '' }}
                onChange={jest.fn()}
                validationErrors={{}}
            />
        );

        // Should show placeholder text when no preview is available
        expect(screen.getByText('Configure a schedule to see preview')).toBeInTheDocument();
    });
});