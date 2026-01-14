import { useEffect, useMemo, useState } from 'react';
import {
    Box,
    Stack,
    Typography,
    Alert,
    Paper,
    GlobalStyles,
    List,
    ListItem,
    ListItemIcon,
    ListItemText,
    CircularProgress,
} from '@mui/material';
import {
    Schedule as ScheduleIcon,
    Public as TimezoneIcon,
    Event as EventIcon,
    Info as InfoIcon,
} from '@mui/icons-material';
import { Cron } from 'react-js-cron';
import 'react-js-cron/dist/styles.css';
import TimezoneSelector from './TimezoneSelector';
import { getSchedulePreview } from '../../utils/schedulePreview';

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
    
    // State for schedule preview
    const [preview, setPreview] = useState(null);
    const [previewError, setPreviewError] = useState(null);
    const [isLoadingPreview, setIsLoadingPreview] = useState(false);
    
    // State for advanced mode toggle (removed for now, will be added in later tasks)

    // Detect browser timezone as default
    const browserTimezone = useMemo(() => {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch (error) {
            return 'UTC';
        }
    }, []);


    // Update preview in real-time when cron or timezone changes
    useEffect(() => {
        // Only generate preview if both cron and timezone are set
        if (!cron || !timezone) {
            setPreview(null);
            setPreviewError(null);
            return;
        }

        setIsLoadingPreview(true);
        setPreviewError(null);

        // Use a small delay to debounce rapid changes
        const timeoutId = setTimeout(() => {
            try {
                const schedulePreview = getSchedulePreview(cron, timezone, 5);
                setPreview(schedulePreview);
                setPreviewError(null);
            } catch (error) {
                setPreview(null);
                setPreviewError(error.message || 'Unable to generate schedule preview');
            } finally {
                setIsLoadingPreview(false);
            }
        }, 300);

        return () => clearTimeout(timeoutId);
    }, [cron, timezone]);

    const handleCronChange = (newCron) => {
        // Only trigger onChange if the cron expression actually changed
        if (newCron !== cron) {
            onChange({
                cron: newCron,
                timezone: timezone,
            });
        }
    };

    const handleTimezoneChange = (selectedTimezone) => {
        onChange({
            cron,
            timezone: selectedTimezone,
        });
    };

    return (
        <>
            {/* Global styles to override Ant Design components */}
            <GlobalStyles
                styles={(theme) => ({
                    // Target Ant Design dropdown menus that are rendered in portals
                    '.ant-select-dropdown': {
                        backgroundColor: `${theme.palette.background.paper} !important`,
                        color: `${theme.palette.text.primary} !important`,
                        borderRadius: '4px',
                        boxShadow: theme.shadows[8],
                        
                        '& .ant-select-item': {
                            color: `${theme.palette.text.primary} !important`,
                            padding: '8px 12px',
                            
                            '&:hover': {
                                backgroundColor: `${theme.palette.action.hover} !important`,
                            },
                            
                            '&.ant-select-item-option-selected': {
                                backgroundColor: `${theme.palette.action.selected} !important`,
                                fontWeight: 500,
                            },
                        },
                    },
                    
                    // Target Ant Design select boxes within react-js-cron
                    '.react-js-cron .ant-select': {
                        background: `${theme.palette.background.default} !important`,
                        color: `${theme.palette.text.primary} !important`,
                        borderColor: `${theme.palette.divider} !important`,
                        borderRadius: '4px !important',

                        '& .ant-select-suffix': {
                            color: `${theme.palette.text.primary} !important`,
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
                    sx={(theme) => ({ 
                        p: 2,
                        '& .react-js-cron': {
                            display: 'flex',
                            flexWrap: 'wrap',
                            gap: 1,
                            alignItems: 'center',
                            
                            // Style the text labels
                            '& > span': {
                                color: theme.palette.text.primary,
                                fontSize: '0.875rem',
                            },
                        },
                    })}
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
                    helperText={timezone ? "Choose the timezone for schedule execution" : `Choose the timezone for schedule execution (detected: ${browserTimezone})`}
                    placeholder={`Select timezone (e.g., ${browserTimezone})`}
                />
            </Box>

            {/* Schedule Preview */}
            <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                    <EventIcon color="primary" />
                    <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                        Schedule Preview
                    </Typography>
                </Box>
                
                <Paper 
                    variant="outlined" 
                    sx={{ 
                        p: 2,
                        bgcolor: 'background.default',
                        border: '1px solid',
                        borderColor: 'divider',
                    }}
                >
                    {isLoadingPreview && (
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                            <CircularProgress size={20} />
                            <Typography variant="body2" color="text.secondary">
                                Calculating next execution times...
                            </Typography>
                        </Box>
                    )}

                    {!isLoadingPreview && previewError && (
                        <Alert 
                            severity="warning" 
                            icon={<InfoIcon />}
                            sx={{ 
                                '& .MuiAlert-message': { 
                                    width: '100%' 
                                } 
                            }}
                        >
                            <Typography variant="body2">
                                {previewError}
                            </Typography>
                        </Alert>
                    )}

                    {!isLoadingPreview && !previewError && preview && (
                        <Stack spacing={2}>
                            {/* Human-readable description */}
                            <Box>
                                <Typography 
                                    variant="body1" 
                                    sx={{ 
                                        fontWeight: 600,
                                        color: 'primary.main',
                                        mb: 1,
                                    }}
                                >
                                    {preview.description}
                                </Typography>
                            </Box>

                            {/* Next execution times */}
                            {preview.nextExecutions && preview.nextExecutions.length > 0 && (
                                <Box>
                                    <Typography 
                                        variant="subtitle2" 
                                        sx={{ 
                                            fontWeight: 600,
                                            mb: 1,
                                            color: 'text.secondary',
                                        }}
                                    >
                                        Next {preview.nextExecutions.length} execution{preview.nextExecutions.length > 1 ? 's' : ''}:
                                    </Typography>
                                    <List dense disablePadding>
                                        {preview.nextExecutions.map((execution, index) => (
                                            <ListItem 
                                                key={index}
                                                disableGutters
                                                sx={{ 
                                                    py: 0.5,
                                                    px: 0,
                                                }}
                                            >
                                                <ListItemIcon sx={{ minWidth: 32 }}>
                                                    <EventIcon 
                                                        fontSize="small" 
                                                        sx={{ color: 'text.secondary' }}
                                                    />
                                                </ListItemIcon>
                                                <ListItemText
                                                    primary={execution}
                                                    primaryTypographyProps={{
                                                        variant: 'body2',
                                                        sx: { 
                                                            fontFamily: 'monospace',
                                                            fontSize: '0.875rem',
                                                        },
                                                    }}
                                                />
                                            </ListItem>
                                        ))}
                                    </List>
                                </Box>
                            )}
                        </Stack>
                    )}

                    {!isLoadingPreview && !previewError && !preview && (
                        <Typography variant="body2" color="text.secondary">
                            Configure a schedule to see preview
                        </Typography>
                    )}
                </Paper>
            </Box>

        </Stack>
        </>
    );
};

export default RecurringScheduleBuilder;