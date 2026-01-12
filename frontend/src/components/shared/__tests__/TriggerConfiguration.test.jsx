import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import TriggerConfiguration from '../TriggerConfiguration';

describe('TriggerConfiguration', () => {
    const defaultProps = {
        triggerType: 'manual',
        triggerConfig: {},
        onTriggerTypeChange: jest.fn(),
        onTriggerConfigChange: jest.fn(),
        validationErrors: {},
        required: false,
        showHelperText: true,
    };

    beforeEach(() => {
        jest.clearAllMocks();
    });

    test('renders trigger type selector', () => {
        render(<TriggerConfiguration {...defaultProps} />);
        
        expect(screen.getByRole('combobox')).toBeInTheDocument();
        expect(screen.getByText('Manual')).toBeInTheDocument();
    });

    test('shows scheduled date input when scheduled trigger is selected', () => {
        render(
            <TriggerConfiguration 
                {...defaultProps} 
                triggerType="scheduled"
                triggerConfig={{ scheduled_at: '2024-01-01T09:00' }}
            />
        );
        
        expect(screen.getByLabelText('Scheduled Date/Time')).toBeInTheDocument();
    });

    test('shows cron expression input when cron trigger is selected', () => {
        render(
            <TriggerConfiguration 
                {...defaultProps} 
                triggerType="cron"
                triggerConfig={{ cron: '0 9 * * *' }}
            />
        );
        
        expect(screen.getByLabelText('Cron Expression')).toBeInTheDocument();
        expect(screen.getByDisplayValue('0 9 * * *')).toBeInTheDocument();
    });

    test('shows info alert for manual trigger', () => {
        render(<TriggerConfiguration {...defaultProps} triggerType="manual" />);
        
        expect(screen.getByText('Manual trigger selected - no additional configuration needed.')).toBeInTheDocument();
    });

    test('calls onTriggerTypeChange when trigger type changes', () => {
        render(<TriggerConfiguration {...defaultProps} />);
        
        const select = screen.getByRole('combobox');
        fireEvent.mouseDown(select);
        
        const scheduledOption = screen.getByText('Scheduled');
        fireEvent.click(scheduledOption);
        
        expect(defaultProps.onTriggerTypeChange).toHaveBeenCalledWith('scheduled');
    });

    test('calls onTriggerConfigChange when config changes', () => {
        render(
            <TriggerConfiguration 
                {...defaultProps} 
                triggerType="cron"
                triggerConfig={{ cron: '0 9 * * *' }}
            />
        );
        
        const cronInput = screen.getByLabelText('Cron Expression');
        fireEvent.change(cronInput, { target: { value: '0 10 * * *' } });
        
        expect(defaultProps.onTriggerConfigChange).toHaveBeenCalledWith({ cron: '0 10 * * *' });
    });

    test('displays validation errors', () => {
        render(
            <TriggerConfiguration 
                {...defaultProps} 
                triggerType="scheduled"
                validationErrors={{ scheduled_at: 'Date is required' }}
            />
        );
        
        expect(screen.getByText('Date is required')).toBeInTheDocument();
    });

    test('shows required indicator when required is true', () => {
        render(<TriggerConfiguration {...defaultProps} required={true} />);
        
        expect(screen.getAllByText(/Trigger Type \*/)).toHaveLength(2); // Label and legend
    });
});