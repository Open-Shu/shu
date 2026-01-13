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
    Box,
    Typography,
} from '@mui/material';
import { Public as TimezoneIcon } from '@mui/icons-material';
import RecurringScheduleBuilder from './RecurringScheduleBuilder';
import TimezoneSelector from './TimezoneSelector';

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
                    <MenuItem value="cron">Recurring</MenuItem>  // We do not call it cron on the frontend to avoid confusion
                </Select>
                {validationErrors.trigger_type && (
                    <FormHelperText>{validationErrors.trigger_type}</FormHelperText>
                )}
            </FormControl>

            {triggerType === 'scheduled' && (
                <>
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
                    <Box>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                            <TimezoneIcon color="primary" />
                            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                                Timezone
                            </Typography>
                        </Box>
                        <TimezoneSelector
                            value={triggerConfig.timezone || ''}
                            onChange={(timezone) => handleConfigChange('timezone', timezone)}
                            error={validationErrors.timezone}
                            helperText="Choose the timezone for the scheduled execution"
                        />
                    </Box>
                </>
            )}

            {triggerType === 'cron' && (
                <RecurringScheduleBuilder
                    value={triggerConfig}
                    onChange={onTriggerConfigChange}
                    validationErrors={validationErrors}
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