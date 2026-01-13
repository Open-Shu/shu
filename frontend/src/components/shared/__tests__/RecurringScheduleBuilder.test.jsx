import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

// Mock the cron library to avoid CSS import issues
jest.mock('@levashovn/react-js-cron-mui5', () => ({
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

import RecurringScheduleBuilder from '../RecurringScheduleBuilder';

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

    test('shows current configuration', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        expect(screen.getByText('Current Configuration')).toBeInTheDocument();
        expect(screen.getByText('0 9 * * *')).toBeInTheDocument();
        expect(screen.getByText('America/New_York')).toBeInTheDocument();
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
});