import React from 'react';
import {
    FormControl,
    InputLabel,
    Select,
    MenuItem,
    TextField,
    Stack,
    Alert,
    FormHelperText,
} from '@mui/material';

/**
 * TriggerConfiguration - Shared component for trigger type and configuration
 * 
 * @param {Object} props - Component props
 * @param {string} props.triggerType - Current trigger type ('manual', 'scheduled', 'cron')
 * @param {Object} props.triggerConfig - Current trigger configuration object
 * @param {function} props.onTriggerTypeChange - Callback when trigger type changes
 * @param {function} props.onTriggerConfigChange - Callback when trigger config changes
 * @param {Object} props.validationErrors - Validation errors object
 * @param {boolean} props.required - Whether trigger type is required
 * @param {boolean} props.showHelperText - Whether to show helper text
 */
const TriggerConfiguration = ({
    triggerType = 'manual',
    triggerConfig = {},
    onTriggerTypeChange,
    onTriggerConfigChange,
    validationErrors = {},
    required = false,
    showHelperText = true,
}) => {
    const handleTriggerTypeChange = (e) => {
        const newType = e.target.value;
        onTriggerTypeChange(newType);
        // Reset config when type changes
        onTriggerConfigChange({});
    };

    const handleConfigChange = (field, value) => {
        const newConfig = { ...triggerConfig, [field]: value };
        onTriggerConfigChange(newConfig);
    };

    return (
        <Stack spacing={2}>
            <FormControl fullWidth error={!!validationErrors.trigger_type}>
                <InputLabel>Trigger Type {required ? '*' : ''}</InputLabel>
                <Select
                    value={triggerType}
                    label={`Trigger Type ${required ? '*' : ''}`}
                    onChange={handleTriggerTypeChange}
                >
                    <MenuItem value="manual">Manual</MenuItem>
                    <MenuItem value="scheduled">Scheduled</MenuItem>
                    <MenuItem value="cron">Cron</MenuItem>
                </Select>
                {validationErrors.trigger_type && (
                    <FormHelperText>{validationErrors.trigger_type}</FormHelperText>
                )}
            </FormControl>

            {triggerType === 'scheduled' && (
                <TextField
                    label="Scheduled Date/Time"
                    type="datetime-local"
                    value={triggerConfig.scheduled_at || ''}
                    onChange={(e) => handleConfigChange('scheduled_at', e.target.value)}
                    fullWidth
                    InputLabelProps={{ shrink: true }}
                    error={!!validationErrors.scheduled_at}
                    helperText={
                        validationErrors.scheduled_at || 
                        (showHelperText ? "One-time execution at the specified date and time" : undefined)
                    }
                />
            )}

            {triggerType === 'cron' && (
                <TextField
                    label="Cron Expression"
                    value={triggerConfig.cron || ''}
                    onChange={(e) => handleConfigChange('cron', e.target.value)}
                    fullWidth
                    placeholder="0 9 * * *"
                    error={!!validationErrors.cron}
                    helperText={
                        validationErrors.cron || 
                        (showHelperText ? "Standard cron expression (e.g., '0 9 * * *' for daily at 9am)" : undefined)
                    }
                />
            )}

            {triggerType === 'manual' && showHelperText && (
                <Alert severity="info">
                    Manual trigger selected - no additional configuration needed.
                </Alert>
            )}
        </Stack>
    );
};

export default TriggerConfiguration;