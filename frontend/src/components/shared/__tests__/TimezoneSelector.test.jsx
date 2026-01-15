import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import TimezoneSelector from '../TimezoneSelector';

describe('TimezoneSelector', () => {
    const defaultProps = {
        value: 'America/New_York',
        onChange: jest.fn(),
    };

    beforeEach(() => {
        jest.clearAllMocks();
    });

    test('renders timezone selector', () => {
        render(<TimezoneSelector {...defaultProps} />);
        
        expect(screen.getByRole('combobox')).toBeInTheDocument();
        expect(screen.getByLabelText('Timezone')).toBeInTheDocument();
    });

    test('displays current timezone value', () => {
        render(<TimezoneSelector {...defaultProps} />);
        
        const input = screen.getByDisplayValue('New York');
        expect(input).toBeInTheDocument();
    });

    test('shows helper text', () => {
        render(<TimezoneSelector {...defaultProps} />);
        
        expect(screen.getByText('Choose the timezone for schedule execution')).toBeInTheDocument();
    });

    test('shows custom helper text', () => {
        render(
            <TimezoneSelector 
                {...defaultProps}
                helperText="Custom helper text"
            />
        );
        
        expect(screen.getByText('Custom helper text')).toBeInTheDocument();
    });

    test('shows error message', () => {
        render(
            <TimezoneSelector 
                {...defaultProps}
                error="Invalid timezone"
            />
        );
        
        expect(screen.getByText('Invalid timezone')).toBeInTheDocument();
    });

    test('shows required indicator', () => {
        render(<TimezoneSelector {...defaultProps} required />);
        
        expect(screen.getByLabelText('Timezone *')).toBeInTheDocument();
    });

    test('handles empty value', () => {
        render(<TimezoneSelector {...defaultProps} value="" />);
        
        const input = screen.getByRole('combobox');
        expect(input).toHaveValue('');
    });

    test('renders with custom placeholder', () => {
        render(
            <TimezoneSelector 
                {...defaultProps}
                value=""
                placeholder="Choose a timezone"
            />
        );
        
        expect(screen.getByPlaceholderText('Choose a timezone')).toBeInTheDocument();
    });

    test('can be set to not full width', () => {
        const { container } = render(
            <TimezoneSelector {...defaultProps} fullWidth={false} />
        );
        
        const autocomplete = container.querySelector('.MuiAutocomplete-root');
        expect(autocomplete).not.toHaveClass('MuiAutocomplete-fullWidth');
    });
});
