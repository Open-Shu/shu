import { useEffect, useMemo } from 'react';
import {
    Box,
    Stack,
    Typography,
    Alert,
    Paper,
    Chip,
    GlobalStyles,
} from '@mui/material';
import {
    Schedule as ScheduleIcon,
    Public as TimezoneIcon,
} from '@mui/icons-material';
import { Cron } from '@levashovn/react-js-cron-mui5';
import TimezoneSelector from './TimezoneSelector';

/**
 * RecurringScheduleBuilder - A wrapper around the cron library that provides
 * a user-friendly interface for creating recurring schedules
 * 
 * @param {Object} props - Component props
 * @param {Object} props.value - Current trigger configuration with cron and timezone
 * @param {string} props.value.cron - Current cron expression
 * @param {string} props.value.timezone - Current timezone (IANA format)
 * @param {function} props.onChange - Callback when configuration changes
 * @param {Object} props.validationErrors - Validation errors object
 * @param {Object} props.validationErrors.cron - Cron expression validation error
 * @param {Object} props.validationErrors.timezone - Timezone validation error
 */
const RecurringScheduleBuilder = ({
    value = {},
    onChange,
    validationErrors = {},
}) => {
    const { cron = '0 9 * * *', timezone = '' } = value;
    
    // State for advanced mode toggle (removed for now, will be added in later tasks)

    // Detect browser timezone as default
    const browserTimezone = useMemo(() => {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch (error) {
            return 'UTC';
        }
    }, []);

    // Initialize timezone if not set
    useEffect(() => {
        if (!timezone && browserTimezone) {
            onChange({
                cron,
                timezone: browserTimezone,
            });
        }
    }, [timezone, browserTimezone, cron, onChange]);

    const handleCronChange = (newCron) => {
        onChange({
            cron: newCron,
            timezone: timezone || browserTimezone,
        });
    };

    const handleTimezoneChange = (selectedTimezone) => {
        onChange({
            cron,
            timezone: selectedTimezone,
        });
    };

    return (
        <>
            {/* Global styles to override cron library dropdown menus */}
            <GlobalStyles
                styles={(theme) => ({
                    // Target MUI Menu components that are rendered in portals
                    '.MuiMenu-paper': {
                        backgroundColor: `${theme.palette.background.paper} !important`,
                        color: `${theme.palette.text.primary} !important`,
                    },
                    '.MuiMenu-list': {
                        backgroundColor: `${theme.palette.background.paper} !important`,
                    },
                    '.MuiPopover-paper': {
                        backgroundColor: `${theme.palette.background.paper} !important`,
                        color: `${theme.palette.text.primary} !important`,
                    },
                    // Target menu items specifically within cron component context
                    '.MuiMenu-paper .MuiMenuItem-root': {
                        backgroundColor: `${theme.palette.background.paper} !important`,
                        color: `${theme.palette.text.primary} !important`,
                        '&:hover': {
                            backgroundColor: `${theme.palette.action.hover} !important`,
                        },
                        '&.Mui-selected': {
                            backgroundColor: `${theme.palette.action.selected} !important`,
                            '&:hover': {
                                backgroundColor: `${theme.palette.action.selected} !important`,
                            },
                        },
                    },
                })}
            />
            <Stack spacing={2}>
            {/* Schedule Builder Section */}
            <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                    <ScheduleIcon color="primary" />
                    <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                        Schedule Configuration
                    </Typography>
                </Box>
                
                <Paper 
                    variant="outlined" 
                    sx={{ 
                        p: 2,
                        '& .react-js-cron': {
                            '& .MuiFormControl-root': {
                                minWidth: 'auto',
                            },
                            '& .MuiSelect-select': {
                                py: 1,
                                backgroundColor: 'background.default',
                                color: 'text.primary',
                            },
                            'div.react-js-cron-select': {
                                marginRight: '5px',
                            },
                            '& .MuiInputBase-root': {
                                fontSize: '0.875rem',
                                backgroundColor: 'background.paper',
                                color: 'text.primary',
                                '& .MuiOutlinedInput-notchedOutline': {
                                    borderColor: 'divider',
                                },
                                '&:hover .MuiOutlinedInput-notchedOutline': {
                                    borderColor: 'primary.main',
                                },
                                '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                                    borderColor: 'primary.main',
                                },
                            },
                            // Style the dropdown arrow icon
                            '& .MuiSelect-icon': {
                                color: 'text.primary',
                            },
                            '& .MuiMenuItem-root': {
                                backgroundColor: 'background.paper',
                                color: 'text.primary',
                                '&:hover': {
                                    backgroundColor: 'action.hover',
                                },
                                '&.Mui-selected': {
                                    backgroundColor: 'action.selected',
                                    '&:hover': {
                                        backgroundColor: 'action.selected',
                                    },
                                },
                            },
                            // Style the dropdown menu container
                            '& .MuiPaper-root': {
                                backgroundColor: 'background.paper',
                                color: 'text.primary',
                            },
                        },
                        // Global override for any MUI Menu that appears within this component
                        '& .MuiMenu-paper': {
                            backgroundColor: 'background.paper !important',
                            color: 'text.primary !important',
                        },
                        '& .MuiList-root': {
                            backgroundColor: 'background.paper !important',
                        },
                        // Additional overrides for stubborn dropdown styling
                        '& .MuiPopover-paper': {
                            backgroundColor: 'background.paper !important',
                            color: 'text.primary !important',
                        },
                        '& .MuiMenu-list': {
                            backgroundColor: 'background.paper !important',
                        },
                    }}
                >
                    <Cron
                        value={cron}
                        setValue={handleCronChange}
                        displayError={false}
                        clearButton={false}
                        clockFormat="24-hour-clock"
                        defaultPeriod="day"
                    />
                    {validationErrors.cron && (
                        <Alert severity="error" sx={{ mt: 2 }}>
                            {validationErrors.cron}
                        </Alert>
                    )}
                </Paper>
            </Box>

            {/* Timezone Selection */}
            <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                    <TimezoneIcon color="primary" />
                    <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                        Timezone
                    </Typography>
                </Box>
                <TimezoneSelector
                    value={timezone}
                    onChange={handleTimezoneChange}
                    error={validationErrors.timezone}
                    helperText="Choose the timezone for schedule execution"
                />
            </Box>

            {/* Current Configuration Display */}
            <Paper 
                variant="outlined" 
                sx={{ 
                    p: 2, 
                    bgcolor: 'action.hover',
                    border: '1px solid',
                    borderColor: 'divider',
                }}
            >
                <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 600, mb: 1.5 }}>
                    Current Configuration
                </Typography>
                <Stack spacing={1.5}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Chip 
                            label="Cron Expression" 
                            size="small" 
                            variant="outlined" 
                            sx={{ minWidth: 120 }}
                        />
                        <Typography variant="body2" sx={{ fontFamily: 'monospace', fontWeight: 500 }}>
                            {cron}
                        </Typography>
                    </Box>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Chip 
                            label="Timezone" 
                            size="small" 
                            variant="outlined" 
                            sx={{ minWidth: 120 }}
                        />
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>
                            {timezone || 'Not selected'}
                        </Typography>
                    </Box>
                </Stack>
            </Paper>
        </Stack>
        </>
    );
};

export default RecurringScheduleBuilder;