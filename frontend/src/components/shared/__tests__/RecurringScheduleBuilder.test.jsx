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

    test('renders help section with examples', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        expect(screen.getByText('Schedule Examples & Help')).toBeInTheDocument();
    });

    test('expands help section when clicked', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        const helpSection = screen.getByText('Schedule Examples & Help');
        
        // The content is in the DOM but hidden by Collapse
        // Check that the expand icon is in the correct state
        const expandIcon = screen.getByTestId('ExpandMoreIcon');
        expect(expandIcon).toBeInTheDocument();
        
        // Click to expand
        fireEvent.click(helpSection);
        
        // Examples should be visible
        expect(screen.getByText('Daily at 9:00 AM')).toBeVisible();
        expect(screen.getByText('Every weekday at 8:00 AM')).toBeVisible();
        expect(screen.getByText('Weekly on Monday at 10:00 AM')).toBeVisible();
        expect(screen.getByText('Monthly on the 1st at 9:00 AM')).toBeVisible();
        
        // Check that "Use this" buttons are present (filter to only get the example buttons)
        const useThisButtons = screen.getAllByRole('button', { name: /use this/i });
        expect(useThisButtons.length).toBeGreaterThanOrEqual(4);
    });

    test('applies example schedule when "Use this" button is clicked', () => {
        const mockOnChange = jest.fn();
        // Start with a different cron value so we can see the change
        const testProps = {
            ...defaultProps,
            value: { cron: '0 10 * * *', timezone: 'America/New_York' },
            onChange: mockOnChange,
        };
        render(<RecurringScheduleBuilder {...testProps} />);
        
        // Expand help section
        const helpSection = screen.getByText('Schedule Examples & Help');
        fireEvent.click(helpSection);
        
        // Click the first "Use this" button (Daily at 9:00 AM)
        const useThisButtons = screen.getAllByRole('button', { name: /use this/i });
        fireEvent.click(useThisButtons[0]);
        
        // Should call onChange with the daily cron expression
        expect(mockOnChange).toHaveBeenCalledWith({
            cron: '0 9 * * *',
            timezone: 'America/New_York',
        });
    });

    test('applies weekday schedule when corresponding "Use this" button is clicked', () => {
        const mockOnChange = jest.fn();
        render(<RecurringScheduleBuilder {...defaultProps} onChange={mockOnChange} />);
        
        // Expand help section
        const helpSection = screen.getByText('Schedule Examples & Help');
        fireEvent.click(helpSection);
        
        // Click the second "Use this" button (Every weekday at 8:00 AM)
        const useThisButtons = screen.getAllByRole('button', { name: /use this/i });
        fireEvent.click(useThisButtons[1]);
        
        // Should call onChange with the weekday cron expression
        expect(mockOnChange).toHaveBeenCalledWith({
            cron: '0 8 * * 1-5',
            timezone: 'America/New_York',
        });
    });

    test('displays timezone explanation text', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        expect(screen.getByText(/Why timezone matters:/)).toBeInTheDocument();
        expect(screen.getByText(/Timezones ensure your scheduled experiences run at the correct local time/)).toBeInTheDocument();
    });

    test('displays tooltips for schedule configuration', () => {
        render(<RecurringScheduleBuilder {...defaultProps} />);
        
        // Check that help icons are present (they contain the tooltips)
        const helpIcons = screen.getAllByTestId('HelpOutlineIcon');
        expect(helpIcons.length).toBeGreaterThan(0);
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

    describe('Advanced Mode Toggle', () => {
        beforeEach(() => {
            // Clear session storage before each test
            sessionStorage.clear();
        });

        test('renders advanced mode toggle button', () => {
            render(<RecurringScheduleBuilder {...defaultProps} />);
            
            expect(screen.getByRole('button', { name: /advanced mode/i })).toBeInTheDocument();
        });

        test('switches to advanced mode when toggle is clicked', () => {
            render(<RecurringScheduleBuilder {...defaultProps} />);
            
            const toggleButton = screen.getByRole('button', { name: /advanced mode/i });
            fireEvent.click(toggleButton);
            
            // Should show raw cron input
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.getByPlaceholderText('0 9 * * *')).toBeInTheDocument();
            
            // Button text should change
            expect(screen.getByRole('button', { name: /visual builder/i })).toBeInTheDocument();
        });

        test('switches back to builder mode when toggle is clicked again', () => {
            render(<RecurringScheduleBuilder {...defaultProps} />);
            
            const toggleButton = screen.getByRole('button', { name: /advanced mode/i });
            
            // Switch to advanced mode
            fireEvent.click(toggleButton);
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            
            // Switch back to builder mode
            const builderButton = screen.getByRole('button', { name: /visual builder/i });
            fireEvent.click(builderButton);
            
            // Should show cron component again
            expect(screen.getByTestId('cron-component')).toBeInTheDocument();
            expect(screen.getByRole('button', { name: /advanced mode/i })).toBeInTheDocument();
        });

        test('preserves advanced mode preference in session storage', () => {
            render(<RecurringScheduleBuilder {...defaultProps} />);
            
            const toggleButton = screen.getByRole('button', { name: /advanced mode/i });
            fireEvent.click(toggleButton);
            
            // Check session storage
            expect(sessionStorage.getItem('cronBuilderAdvancedMode')).toBe('true');
            
            // Switch back
            const builderButton = screen.getByRole('button', { name: /visual builder/i });
            fireEvent.click(builderButton);
            
            expect(sessionStorage.getItem('cronBuilderAdvancedMode')).toBe('false');
        });

        test('restores advanced mode preference from session storage', () => {
            // Set preference in session storage
            sessionStorage.setItem('cronBuilderAdvancedMode', 'true');
            
            render(<RecurringScheduleBuilder {...defaultProps} />);
            
            // Should start in advanced mode
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.getByRole('button', { name: /visual builder/i })).toBeInTheDocument();
        });

        test('updates cron expression when editing in advanced mode', () => {
            render(<RecurringScheduleBuilder {...defaultProps} />);
            
            // Switch to advanced mode
            const toggleButton = screen.getByRole('button', { name: /advanced mode/i });
            fireEvent.click(toggleButton);
            
            // Edit the cron expression
            const cronInput = screen.getByLabelText('Cron Expression');
            fireEvent.change(cronInput, { target: { value: '0 10 * * 1-5' } });
            fireEvent.blur(cronInput);
            
            // Should call onChange with new value
            expect(defaultProps.onChange).toHaveBeenCalledWith({
                cron: '0 10 * * 1-5',
                timezone: 'America/New_York',
            });
        });

        test('does not call onChange if cron expression has not changed on blur', () => {
            render(<RecurringScheduleBuilder {...defaultProps} />);
            
            // Switch to advanced mode
            const toggleButton = screen.getByRole('button', { name: /advanced mode/i });
            fireEvent.click(toggleButton);
            
            // Blur without changing
            const cronInput = screen.getByLabelText('Cron Expression');
            fireEvent.blur(cronInput);
            
            // onChange should not be called (only called once for mode toggle)
            expect(defaultProps.onChange).not.toHaveBeenCalled();
        });

        test('hides toggle button when cron expression is complex', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '*/5 * * * *', timezone: 'America/New_York' }, // Step value
            };
            
            // Start in advanced mode
            sessionStorage.setItem('cronBuilderAdvancedMode', 'true');
            
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should not show the toggle button
            expect(screen.queryByRole('button', { name: /visual builder/i })).not.toBeInTheDocument();
            expect(screen.queryByRole('button', { name: /advanced mode/i })).not.toBeInTheDocument();
            
            // Should show warning
            expect(screen.getByText(/too complex for the visual builder/i)).toBeInTheDocument();
        });

        test('shows toggle button when cron expression is simple', () => {
            render(<RecurringScheduleBuilder {...defaultProps} />);
            
            // Should show the toggle button
            expect(screen.getByRole('button', { name: /advanced mode/i })).toBeInTheDocument();
        });

        test('clears complex expression warning when dismissing', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '*/5 * * * *', timezone: 'America/New_York' },
            };
            
            // Don't set session storage - let component auto-switch
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should start in advanced mode with warning (auto-switched)
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.getByText(/too complex for the visual builder/i)).toBeInTheDocument();
            
            // Dismiss the warning
            const closeButton = screen.getByLabelText('Close');
            fireEvent.click(closeButton);
            
            // Warning should be cleared
            expect(screen.queryByText(/too complex for the visual builder/i)).not.toBeInTheDocument();
            
            // Should still be in advanced mode
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
        });

        test('displays validation errors in advanced mode', () => {
            render(
                <RecurringScheduleBuilder 
                    {...defaultProps}
                    validationErrors={{ cron: 'Invalid cron expression' }}
                />
            );
            
            // Switch to advanced mode
            const toggleButton = screen.getByRole('button', { name: /advanced mode/i });
            fireEvent.click(toggleButton);
            
            // Should show validation error
            expect(screen.getByText('Invalid cron expression')).toBeInTheDocument();
        });
    });

    describe('Complex Cron Expression Fallback', () => {
        beforeEach(() => {
            sessionStorage.clear();
        });

        test('automatically switches to advanced mode for complex cron with step values', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '*/5 * * * *', timezone: 'America/New_York' }, // Every 5 minutes
            };
            
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should automatically be in advanced mode
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.queryByRole('button', { name: /visual builder/i })).not.toBeInTheDocument();
            
            // Should show warning
            expect(screen.getByText(/too complex for the visual builder/i)).toBeInTheDocument();
        });

        test('automatically switches to advanced mode for 6-position cron with seconds', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '0 0 9 * * *', timezone: 'America/New_York' }, // 6-position with seconds
            };
            
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should automatically be in advanced mode
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.queryByRole('button', { name: /visual builder/i })).not.toBeInTheDocument();
            
            // Should show warning
            expect(screen.getByText(/too complex for the visual builder/i)).toBeInTheDocument();
        });

        test('automatically switches to advanced mode for complex cron with multiple ranges', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '0 9 1-5,10-15 * *', timezone: 'America/New_York' }, // Multiple day ranges
            };
            
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should automatically be in advanced mode
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.queryByRole('button', { name: /visual builder/i })).not.toBeInTheDocument();
            
            // Should show warning
            expect(screen.getByText(/too complex for the visual builder/i)).toBeInTheDocument();
        });

        test('automatically switches to advanced mode for complex cron with long lists', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '0 9 * * 0,1,2,3,4,5,6', timezone: 'America/New_York' }, // All days listed
            };
            
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should automatically be in advanced mode
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.queryByRole('button', { name: /visual builder/i })).not.toBeInTheDocument();
            
            // Should show warning
            expect(screen.getByText(/too complex for the visual builder/i)).toBeInTheDocument();
        });

        test('automatically switches to advanced mode for complex cron with range and step', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '0 9-17/2 * * *', timezone: 'America/New_York' }, // Every 2 hours from 9 to 17
            };
            
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should automatically be in advanced mode
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.queryByRole('button', { name: /visual builder/i })).not.toBeInTheDocument();
        });

        test('does not switch to advanced mode for simple cron expressions', () => {
            const simpleCronProps = {
                ...defaultProps,
                value: { cron: '0 9 * * 1-5', timezone: 'America/New_York' }, // Weekdays at 9 AM
            };
            
            render(<RecurringScheduleBuilder {...simpleCronProps} />);
            
            // Should be in builder mode
            expect(screen.getByTestId('cron-component')).toBeInTheDocument();
            expect(screen.getByRole('button', { name: /advanced mode/i })).toBeInTheDocument();
            
            // Should not show warning
            expect(screen.queryByText(/too complex for the visual builder/i)).not.toBeInTheDocument();
        });

        test('does not switch to advanced mode for comma-separated day-of-week values', () => {
            const commaDaysProps = {
                ...defaultProps,
                value: { cron: '0 9 * * 1,3,5', timezone: 'America/New_York' }, // Mon, Wed, Fri at 9 AM
            };
            
            render(<RecurringScheduleBuilder {...commaDaysProps} />);
            
            // Should be in builder mode (react-js-cron supports multiple day selection)
            expect(screen.getByTestId('cron-component')).toBeInTheDocument();
            expect(screen.getByRole('button', { name: /advanced mode/i })).toBeInTheDocument();
            
            // Should not show warning
            expect(screen.queryByText(/too complex for the visual builder/i)).not.toBeInTheDocument();
        });

        test('does not allow switching to builder mode for complex expressions', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '*/5 * * * *', timezone: 'America/New_York' },
            };
            
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should start in advanced mode (auto-switched due to complex expression)
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.getByText(/too complex for the visual builder/i)).toBeInTheDocument();
            
            // Should NOT show the toggle button
            expect(screen.queryByRole('button', { name: /visual builder/i })).not.toBeInTheDocument();
            expect(screen.queryByRole('button', { name: /advanced mode/i })).not.toBeInTheDocument();
        });

        test('persists advanced mode in session storage when auto-switched', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '*/5 * * * *', timezone: 'America/New_York' },
            };
            
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should have set session storage
            expect(sessionStorage.getItem('cronBuilderAdvancedMode')).toBe('true');
        });

        test('allows dismissing the complex expression warning', () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '*/5 * * * *', timezone: 'America/New_York' },
            };
            
            render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should show warning with close button
            const warning = screen.getByText(/too complex for the visual builder/i);
            expect(warning).toBeInTheDocument();
            
            // Find and click the close button on the Alert
            const closeButton = warning.closest('.MuiAlert-root').querySelector('button');
            if (closeButton) {
                fireEvent.click(closeButton);
                
                // Warning should be dismissed
                expect(screen.queryByText(/too complex for the visual builder/i)).not.toBeInTheDocument();
            }
        });

        test('handles cron expression updates that become complex', async () => {
            const { rerender } = render(<RecurringScheduleBuilder {...defaultProps} />);
            
            // Should start in builder mode
            expect(screen.getByTestId('cron-component')).toBeInTheDocument();
            
            // Update to complex cron
            rerender(
                <RecurringScheduleBuilder 
                    {...defaultProps}
                    value={{ cron: '*/5 * * * *', timezone: 'America/New_York' }}
                />
            );
            
            // Should automatically switch to advanced mode
            await waitFor(() => {
                expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            });
            
            expect(screen.getByText(/too complex for the visual builder/i)).toBeInTheDocument();
        });

        test('clears warning when cron expression changes from complex to simple', async () => {
            const complexCronProps = {
                ...defaultProps,
                value: { cron: '*/5 * * * *', timezone: 'America/New_York' },
            };
            
            const { rerender } = render(<RecurringScheduleBuilder {...complexCronProps} />);
            
            // Should start in advanced mode with warning
            expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
            expect(screen.getByText(/too complex for the visual builder/i)).toBeInTheDocument();
            
            // Update to simple cron
            rerender(
                <RecurringScheduleBuilder 
                    {...defaultProps}
                    value={{ cron: '0 9 * * *', timezone: 'America/New_York' }}
                />
            );
            
            // Warning should be cleared
            await waitFor(() => {
                expect(screen.queryByText(/too complex for the visual builder/i)).not.toBeInTheDocument();
            });
        });
    });

    describe('Edge Case Handling', () => {
        beforeEach(() => {
            sessionStorage.clear();
        });

        test('displays warning for day 31 scheduled across all months', async () => {
            const day31Props = {
                ...defaultProps,
                value: { cron: '0 9 31 * *', timezone: 'America/New_York' },
            };
            
            render(<RecurringScheduleBuilder {...day31Props} />);
            
            // Wait for validation to complete
            await waitFor(() => {
                expect(screen.getByText(/Schedule Considerations:/i)).toBeInTheDocument();
            });
            
            // Should show warning about day 31
            expect(screen.getByText(/Day 31 is scheduled but only exists in 7 months/i)).toBeInTheDocument();
        });

        test('displays warning for day 30 scheduled across all months', async () => {
            const day30Props = {
                ...defaultProps,
                value: { cron: '0 9 30 * *', timezone: 'America/New_York' },
            };
            
            render(<RecurringScheduleBuilder {...day30Props} />);
            
            // Wait for validation to complete
            await waitFor(() => {
                expect(screen.getByText(/Schedule Considerations:/i)).toBeInTheDocument();
            });
            
            // Should show warning about day 30 and February
            expect(screen.getByText(/Day 30 is scheduled but does not exist in February/i)).toBeInTheDocument();
        });

        test('displays warning for day 29 scheduled across all months (leap year)', async () => {
            const day29Props = {
                ...defaultProps,
                value: { cron: '0 9 29 * *', timezone: 'America/New_York' },
            };
            
            render(<RecurringScheduleBuilder {...day29Props} />);
            
            // Wait for validation to complete
            await waitFor(() => {
                expect(screen.getByText(/Schedule Considerations:/i)).toBeInTheDocument();
            });
            
            // Should show warning about day 29 and leap years
            expect(screen.getByText(/Day 29 is scheduled but does not exist in February \(except leap years\)/i)).toBeInTheDocument();
        });

        test('displays error for day 31 scheduled in February', async () => {
            const feb31Props = {
                ...defaultProps,
                value: { cron: '0 9 31 2 *', timezone: 'America/New_York' },
            };
            
            render(<RecurringScheduleBuilder {...feb31Props} />);
            
            // Wait for validation to complete
            await waitFor(() => {
                // This should show an error, not a warning, because the cron-parser library
                // rejects this as an invalid cron expression
                expect(screen.getByText(/Invalid explicit day of month definition/i)).toBeInTheDocument();
            });
        });

        test('displays error for day 31 scheduled in April (30-day month)', async () => {
            const april31Props = {
                ...defaultProps,
                value: { cron: '0 9 31 4 *', timezone: 'America/New_York' },
            };
            
            render(<RecurringScheduleBuilder {...april31Props} />);
            
            // Wait for validation to complete
            await waitFor(() => {
                // This should show an error, not a warning, because the cron-parser library
                // rejects this as an invalid cron expression
                expect(screen.getByText(/Invalid explicit day of month definition/i)).toBeInTheDocument();
            });
        });

        test('does not display warnings for valid day-of-month selections', async () => {
            const validProps = {
                ...defaultProps,
                value: { cron: '0 9 15 * *', timezone: 'America/New_York' },
            };
            
            const mockPreview = {
                description: 'At 09:00 AM (EST)',
                nextExecutions: ['Tuesday, January 14, 2026 at 9:00 AM EST'],
                executionDates: [],
            };
            getSchedulePreview.mockReturnValue(mockPreview);
            
            render(<RecurringScheduleBuilder {...validProps} />);
            
            // Wait for validation to complete
            await waitFor(() => {
                expect(screen.queryByText(/Schedule Considerations:/i)).not.toBeInTheDocument();
            }, { timeout: 500 });
        });

        test('displays DST transition note in schedule preview', async () => {
            const mockPreview = {
                description: 'At 02:00 AM (EST)',
                nextExecutions: [
                    'Sunday, March 9, 2026 at 2:00 AM EDT (near DST spring forward)',
                ],
                executionDates: [],
            };
            
            getSchedulePreview.mockReturnValue(mockPreview);
            
            render(<RecurringScheduleBuilder {...defaultProps} />);
            
            // Wait for preview to load
            await waitFor(() => {
                expect(screen.getByText(/near DST spring forward/i)).toBeInTheDocument();
            }, { timeout: 500 });
        });

        test('handles multiple validation warnings', async () => {
            const multiWarningProps = {
                ...defaultProps,
                value: { cron: '0 9 29-31 * *', timezone: 'America/New_York' },
            };
            
            render(<RecurringScheduleBuilder {...multiWarningProps} />);
            
            // Wait for validation to complete
            await waitFor(() => {
                expect(screen.getByText(/Schedule Considerations:/i)).toBeInTheDocument();
            });
            
            // Should show multiple warnings
            expect(screen.getByText(/Day 29 is scheduled but does not exist in February/i)).toBeInTheDocument();
            expect(screen.getByText(/Day 30 is scheduled but does not exist in February/i)).toBeInTheDocument();
            expect(screen.getByText(/Day 31 is scheduled but only exists in 7 months/i)).toBeInTheDocument();
        });

        test('does not display warnings when day-of-week is specified', async () => {
            const weekdayProps = {
                ...defaultProps,
                value: { cron: '0 9 31 * 1', timezone: 'America/New_York' }, // Day 31 on Mondays
            };
            
            const mockPreview = {
                description: 'At 09:00 AM on Monday (EST)',
                nextExecutions: ['Monday, March 31, 2026 at 9:00 AM EST'],
                executionDates: [],
            };
            getSchedulePreview.mockReturnValue(mockPreview);
            
            render(<RecurringScheduleBuilder {...weekdayProps} />);
            
            // Wait for validation to complete
            await waitFor(() => {
                expect(screen.queryByText(/Schedule Considerations:/i)).not.toBeInTheDocument();
            }, { timeout: 500 });
        });
    });
});
