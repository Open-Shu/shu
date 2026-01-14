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
    TextField,
    Button,
    Tooltip,
    IconButton,
    Collapse,
} from '@mui/material';
import {
    Schedule as ScheduleIcon,
    Public as TimezoneIcon,
    Event as EventIcon,
    Info as InfoIcon,
    Code as CodeIcon,
    ViewModule as BuilderIcon,
    HelpOutline as HelpIcon,
    ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import { Cron } from 'react-js-cron';
import 'react-js-cron/dist/styles.css';
import TimezoneSelector from './TimezoneSelector';

/**
 * Note: The react-js-cron library (v5.2.0) generates a React warning about the 
 * `dropdownAlign` prop being passed to a DOM element. This is a known issue with 
 * the library and does not affect functionality. The warning can be safely ignored.
 * 
 * Issue: https://github.com/xrutayisire/react-js-cron/issues
 * 
 * If this becomes problematic, consider:
 * 1. Updating to a newer version of react-js-cron when available
 * 2. Switching to an alternative cron builder library
 * 3. Creating a custom wrapper component that filters invalid props
 */
import { getSchedulePreview } from '../../utils/schedulePreview';
import { validateCronExpression, validateTimezone, validateScheduleConfig, validateDayOfMonthEdgeCases } from '../../utils/scheduleValidation';

/**
 * Check if a cron expression is too complex for the visual builder
 * 
 * Complex expressions include:
 * - 6-position cron expressions with seconds field (e.g., "0 0 9 * * *")
 * - Step values (e.g., star-slash-5 for "every 5 minutes")
 * - Ranges with steps (e.g., 1-10/2 for "every 2nd value from 1 to 10")
 * - Multiple ranges (e.g., 1-5,10-15 for "1 through 5 and 10 through 15")
 * - Long lists with many specific values (e.g., 1,2,3,4,5,6,7,8,9,10)
 * - Non-standard field values that the builder can't represent
 * 
 * @param {string} cronExpr - The cron expression to check
 * @returns {boolean} - True if the expression is too complex for the builder
 */
const isComplexCronExpression = (cronExpr) => {
    if (!cronExpr) return false;
    
    const parts = cronExpr.trim().split(/\s+/);
    
    // 6-position cron expressions (with seconds) are not supported by the builder
    if (parts.length === 6) return true;
    
    // Only standard 5-position cron expressions can be handled by the builder
    if (parts.length !== 5) return false;
    
    // Check for step values (*/n or x-y/n)
    const hasStepValues = parts.some(part => part.includes('/'));
    
    // Check for multiple ranges (x-y,a-b)
    const hasMultipleRanges = parts.some(part => {
        const commaCount = (part.match(/,/g) || []).length;
        const rangeCount = (part.match(/-/g) || []).length;
        return commaCount > 0 && rangeCount > 0;
    });
    
    // Check for long lists (more than 5 specific values)
    const hasLongLists = parts.some(part => {
        if (part === '*' || part.includes('-') || part.includes('/')) return false;
        const values = part.split(',');
        return values.length > 5;
    });
    
    // Check for complex day-of-week patterns (e.g., "1-5" for weekdays is OK, but "1,3,5" is complex)
    const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
    const hasComplexDayOfWeek = dayOfWeek !== '*' && dayOfWeek.includes(',') && !dayOfWeek.includes('-');
    
    return hasStepValues || hasMultipleRanges || hasLongLists || hasComplexDayOfWeek;
};

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
    
    // State for advanced mode toggle
    const [isAdvancedMode, setIsAdvancedMode] = useState(() => {
        // Check if the cron expression is complex
        const isComplex = cron && isComplexCronExpression(cron);
        
        // If complex, always start in advanced mode
        if (isComplex) {
            sessionStorage.setItem('cronBuilderAdvancedMode', 'true');
            return true;
        }
        
        // Otherwise, restore preference from session storage
        const savedMode = sessionStorage.getItem('cronBuilderAdvancedMode');
        return savedMode === 'true';
    });
    
    // State for raw cron input in advanced mode
    const [rawCronInput, setRawCronInput] = useState(cron);
    
    // State for complex expression warning
    const [complexExpressionWarning, setComplexExpressionWarning] = useState(() => {
        // Show warning on mount if expression is complex and we're in advanced mode
        if (cron && isComplexCronExpression(cron)) {
            return 'This cron expression is too complex for the visual builder and must be edited in Advanced mode.';
        }
        return null;
    });
    
    // State for help section expansion
    const [showExamples, setShowExamples] = useState(false);

    // Detect browser timezone as default
    const browserTimezone = useMemo(() => {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch (error) {
            return 'UTC';
        }
    }, []);

    // Validate cron expression and timezone in real-time
    const [internalValidationErrors, setInternalValidationErrors] = useState({});
    const [validationWarnings, setValidationWarnings] = useState([]);
    
    useEffect(() => {
        const errors = {};
        const warnings = [];
        
        // Always try to validate schedule configuration for warnings, even if cron is invalid
        // This allows us to show helpful warnings about day-of-month issues
        if (cron && timezone) {
            const scheduleValidation = validateScheduleConfig({ cron, timezone });
            
            if (!scheduleValidation.isValid) {
                Object.assign(errors, scheduleValidation.errors);
            }
            
            if (scheduleValidation.warnings && scheduleValidation.warnings.length > 0) {
                warnings.push(...scheduleValidation.warnings);
            }
        } else {
            // Validate individually if only one is set
            if (cron) {
                const cronValidation = validateCronExpression(cron);
                if (!cronValidation.isValid) {
                    errors.cron = cronValidation.error;
                }
                
                // Still check for day-of-month warnings even if cron is invalid
                // This helps users understand why their cron expression might be invalid
                const dayValidation = validateDayOfMonthEdgeCases(cron);
                if (dayValidation.warnings && dayValidation.warnings.length > 0) {
                    warnings.push(...dayValidation.warnings);
                }
            }
            
            if (timezone) {
                const timezoneValidation = validateTimezone(timezone);
                if (!timezoneValidation.isValid) {
                    errors.timezone = timezoneValidation.error;
                }
            }
        }
        
        setInternalValidationErrors(errors);
        setValidationWarnings(warnings);
    }, [cron, timezone]);
    
    // Merge external validation errors with internal ones
    const mergedValidationErrors = useMemo(() => {
        return {
            ...internalValidationErrors,
            ...validationErrors, // External errors take precedence
        };
    }, [internalValidationErrors, validationErrors]);

    // Sync raw cron input with prop value when it changes externally
    useEffect(() => {
        setRawCronInput(cron);
    }, [cron]);

    // Automatically switch to advanced mode if the cron expression becomes complex
    // Clear warning if expression becomes simple
    // This runs when cron changes after mount
    useEffect(() => {
        if (cron && isComplexCronExpression(cron) && !isAdvancedMode) {
            setIsAdvancedMode(true);
            sessionStorage.setItem('cronBuilderAdvancedMode', 'true');
            setComplexExpressionWarning(
                'This cron expression is too complex for the visual builder and must be edited in Advanced mode.'
            );
        } else if (cron && !isComplexCronExpression(cron)) {
            // Clear warning if expression becomes simple
            setComplexExpressionWarning(null);
        }
    }, [cron]); // Only depend on cron, not isAdvancedMode

    // Update preview in real-time when cron or timezone changes
    useEffect(() => {
        // Only generate preview if both cron and timezone are set
        if (!cron || !timezone) {
            setPreview(null);
            setPreviewError(null);
            setIsLoadingPreview(false); // Clear loading state
            return;
        }

        // Don't generate preview if there are validation errors
        const hasCronError = mergedValidationErrors.cron;
        const hasTimezoneError = mergedValidationErrors.timezone;
        
        if (hasCronError || hasTimezoneError) {
            setPreview(null);
            setPreviewError('Please fix validation errors before generating preview');
            setIsLoadingPreview(false);
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
    }, [cron, timezone, mergedValidationErrors.cron, mergedValidationErrors.timezone]);

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

    const handleAdvancedModeToggle = () => {
        const newMode = !isAdvancedMode;
        
        setIsAdvancedMode(newMode);
        
        // Persist the user's choice in session storage
        sessionStorage.setItem('cronBuilderAdvancedMode', newMode.toString());
        
        // Clear any warnings when switching modes
        setComplexExpressionWarning(null);
    };

    const handleRawCronChange = (event) => {
        const newCron = event.target.value;
        setRawCronInput(newCron);
    };

    const handleRawCronBlur = () => {
        // Update the parent component when user finishes editing
        if (rawCronInput !== cron) {
            onChange({
                cron: rawCronInput,
                timezone: timezone,
            });
        }
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
                        // Ensure dropdown appears above Material-UI Dialog (z-index: 1300)
                        zIndex: '1400 !important',
                        
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
            {/* Help Section with Examples */}
            <Paper 
                variant="outlined" 
                sx={{ 
                    p: 2,
                    bgcolor: 'info.lighter',
                    borderColor: 'info.main',
                }}
            >
                <Box 
                    sx={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        justifyContent: 'space-between',
                        cursor: 'pointer',
                    }}
                    onClick={() => setShowExamples(!showExamples)}
                >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <HelpIcon color="info" />
                        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                            Schedule Examples & Help
                        </Typography>
                    </Box>
                    <IconButton 
                        size="small"
                        sx={{
                            transform: showExamples ? 'rotate(180deg)' : 'rotate(0deg)',
                            transition: 'transform 0.3s',
                        }}
                    >
                        <ExpandMoreIcon />
                    </IconButton>
                </Box>
                
                <Collapse in={showExamples}>
                    <Box sx={{ mt: 2 }}>
                        <Typography variant="body2" color="text.secondary" paragraph>
                            Click "Use this" to apply a common schedule pattern, then adjust as needed.
                        </Typography>
                        
                        <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
                            Common Schedule Examples:
                        </Typography>
                        
                        <List dense disablePadding>
                            <ListItem 
                                disableGutters 
                                sx={{ 
                                    py: 0.5,
                                    display: 'flex',
                                    alignItems: 'flex-start',
                                }}
                            >
                                <ListItemIcon sx={{ minWidth: 32, mt: 0.5 }}>
                                    <EventIcon fontSize="small" color="info" />
                                </ListItemIcon>
                                <ListItemText
                                    primary="Daily at 9:00 AM"
                                    secondary="Perfect for morning briefings or daily reports"
                                    primaryTypographyProps={{ variant: 'body2', fontWeight: 600 }}
                                    secondaryTypographyProps={{ variant: 'caption' }}
                                    sx={{ flex: 1 }}
                                />
                                <Button
                                    size="small"
                                    variant="outlined"
                                    onClick={() => handleCronChange('0 9 * * *')}
                                    sx={{ ml: 2, textTransform: 'none', minWidth: 80 }}
                                >
                                    Use this
                                </Button>
                            </ListItem>
                            
                            <ListItem 
                                disableGutters 
                                sx={{ 
                                    py: 0.5,
                                    display: 'flex',
                                    alignItems: 'flex-start',
                                }}
                            >
                                <ListItemIcon sx={{ minWidth: 32, mt: 0.5 }}>
                                    <EventIcon fontSize="small" color="info" />
                                </ListItemIcon>
                                <ListItemText
                                    primary="Every weekday at 8:00 AM"
                                    secondary="Monday through Friday, ideal for work-related tasks"
                                    primaryTypographyProps={{ variant: 'body2', fontWeight: 600 }}
                                    secondaryTypographyProps={{ variant: 'caption' }}
                                    sx={{ flex: 1 }}
                                />
                                <Button
                                    size="small"
                                    variant="outlined"
                                    onClick={() => handleCronChange('0 8 * * 1-5')}
                                    sx={{ ml: 2, textTransform: 'none', minWidth: 80 }}
                                >
                                    Use this
                                </Button>
                            </ListItem>
                            
                            <ListItem 
                                disableGutters 
                                sx={{ 
                                    py: 0.5,
                                    display: 'flex',
                                    alignItems: 'flex-start',
                                }}
                            >
                                <ListItemIcon sx={{ minWidth: 32, mt: 0.5 }}>
                                    <EventIcon fontSize="small" color="info" />
                                </ListItemIcon>
                                <ListItemText
                                    primary="Weekly on Monday at 10:00 AM"
                                    secondary="Great for weekly summaries or status updates"
                                    primaryTypographyProps={{ variant: 'body2', fontWeight: 600 }}
                                    secondaryTypographyProps={{ variant: 'caption' }}
                                    sx={{ flex: 1 }}
                                />
                                <Button
                                    size="small"
                                    variant="outlined"
                                    onClick={() => handleCronChange('0 10 * * 1')}
                                    sx={{ ml: 2, textTransform: 'none', minWidth: 80 }}
                                >
                                    Use this
                                </Button>
                            </ListItem>
                            
                            <ListItem 
                                disableGutters 
                                sx={{ 
                                    py: 0.5,
                                    display: 'flex',
                                    alignItems: 'flex-start',
                                }}
                            >
                                <ListItemIcon sx={{ minWidth: 32, mt: 0.5 }}>
                                    <EventIcon fontSize="small" color="info" />
                                </ListItemIcon>
                                <ListItemText
                                    primary="Monthly on the 1st at 9:00 AM"
                                    secondary="Perfect for monthly reports or billing reminders"
                                    primaryTypographyProps={{ variant: 'body2', fontWeight: 600 }}
                                    secondaryTypographyProps={{ variant: 'caption' }}
                                    sx={{ flex: 1 }}
                                />
                                <Button
                                    size="small"
                                    variant="outlined"
                                    onClick={() => handleCronChange('0 9 1 * *')}
                                    sx={{ ml: 2, textTransform: 'none', minWidth: 80 }}
                                >
                                    Use this
                                </Button>
                            </ListItem>
                        </List>
                        
                        <Alert severity="info" icon={<InfoIcon />} sx={{ mt: 2 }}>
                            <Typography variant="caption" component="div">
                                <strong>Tip:</strong> After applying an example, you can adjust the time and days using the visual builder.
                            </Typography>
                        </Alert>
                    </Box>
                </Collapse>
            </Paper>
            
            {/* Advanced Mode Toggle */}
            {!isComplexCronExpression(cron) && (
                <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <Tooltip 
                        title={isAdvancedMode 
                            ? "Switch to visual builder for easier schedule configuration" 
                            : "Switch to advanced mode to edit cron expressions directly"
                        }
                    >
                        <Button
                            variant="outlined"
                            size="small"
                            startIcon={isAdvancedMode ? <BuilderIcon /> : <CodeIcon />}
                            onClick={handleAdvancedModeToggle}
                            sx={{ textTransform: 'none' }}
                        >
                            {isAdvancedMode ? 'Visual Builder' : 'Advanced Mode'}
                        </Button>
                    </Tooltip>
                </Box>
            )}

            {/* Complex Expression Warning */}
            {complexExpressionWarning && (
                <Alert 
                    severity="warning" 
                    icon={<InfoIcon />}
                    onClose={() => setComplexExpressionWarning(null)}
                >
                    {complexExpressionWarning}
                </Alert>
            )}

            {/* Schedule Builder Section */}
            <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                    <ScheduleIcon color="primary" />
                    <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                        Schedule Configuration
                    </Typography>
                    <Tooltip 
                        title="Configure when your experience should run automatically. Choose a frequency (daily, weekly, monthly) and set the time."
                        arrow
                        placement="right"
                    >
                        <IconButton size="small" sx={{ ml: 0.5 }}>
                            <HelpIcon fontSize="small" color="action" />
                        </IconButton>
                    </Tooltip>
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
                    {isAdvancedMode ? (
                        // Advanced mode: Raw cron input
                        <Box>
                            <Alert severity="info" icon={<InfoIcon />} sx={{ mb: 2 }}>
                                <Typography variant="body2">
                                    <strong>Advanced Mode:</strong> Enter a standard 5-field cron expression (minute hour day month weekday).
                                    Example: <code>0 9 * * 1-5</code> runs at 9:00 AM on weekdays.
                                </Typography>
                            </Alert>
                            <TextField
                                fullWidth
                                label="Cron Expression"
                                value={rawCronInput}
                                onChange={handleRawCronChange}
                                onBlur={handleRawCronBlur}
                                placeholder="0 9 * * *"
                                helperText="Enter a standard cron expression (minute hour day month weekday)"
                                error={!!mergedValidationErrors.cron}
                                InputProps={{
                                    sx: { fontFamily: 'monospace' }
                                }}
                            />
                            {mergedValidationErrors.cron && (
                                <Alert severity="error" sx={{ mt: 2 }}>
                                    {mergedValidationErrors.cron}
                                </Alert>
                            )}
                        </Box>
                    ) : (
                        // Builder mode: Visual cron builder
                        <>
                            <Alert severity="info" icon={<InfoIcon />} sx={{ mb: 2 }}>
                                <Typography variant="body2">
                                    <strong>Visual Builder:</strong> Select the frequency (daily, weekly, monthly), 
                                    choose the time, and pick specific days if needed. The schedule will be created automatically.
                                </Typography>
                            </Alert>
                            <Cron
                                value={cron}
                                setValue={handleCronChange}
                                displayError={false}
                                clearButton={false}
                                clockFormat="24-hour-clock"
                                defaultPeriod="day"
                            />
                            {mergedValidationErrors.cron && (
                                <Alert severity="error" sx={{ mt: 2 }}>
                                    {mergedValidationErrors.cron}
                                </Alert>
                            )}
                        </>
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
                    <Tooltip 
                        title="Select the timezone for your schedule. The experience will run at the specified time in this timezone, regardless of where the server is located. This ensures your workflows execute at the correct local time."
                        arrow
                        placement="right"
                    >
                        <IconButton size="small" sx={{ ml: 0.5 }}>
                            <HelpIcon fontSize="small" color="action" />
                        </IconButton>
                    </Tooltip>
                </Box>
                
                <Alert severity="info" icon={<InfoIcon />} sx={{ mb: 2 }}>
                    <Typography variant="body2">
                        <strong>Why timezone matters:</strong> Timezones ensure your scheduled experiences run at the correct local time. 
                        For example, "9:00 AM EST" will always run at 9:00 AM Eastern time, even during daylight saving time transitions.
                    </Typography>
                </Alert>
                
                <TimezoneSelector
                    value={timezone}
                    onChange={handleTimezoneChange}
                    error={mergedValidationErrors.timezone}
                    helperText={timezone ? "Choose the timezone for schedule execution" : `Choose the timezone for schedule execution (detected: ${browserTimezone})`}
                    placeholder={`Select timezone (e.g., ${browserTimezone})`}
                />
            </Box>

            {/* Validation Warnings */}
            {validationWarnings.length > 0 && (
                <Alert severity="warning" icon={<InfoIcon />}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
                        Schedule Considerations:
                    </Typography>
                    <Box component="ul" sx={{ m: 0, pl: 2 }}>
                        {validationWarnings.map((warning, index) => (
                            <li key={index}>
                                <Typography variant="body2">{warning}</Typography>
                            </li>
                        ))}
                    </Box>
                </Alert>
            )}

            {/* Schedule Preview */}
            <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                    <EventIcon color="primary" />
                    <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                        Schedule Preview
                    </Typography>
                    <Tooltip 
                        title="Preview shows the next 5 times your experience will run, formatted in your selected timezone. Use this to verify your schedule is configured correctly."
                        arrow
                        placement="right"
                    >
                        <IconButton size="small" sx={{ ml: 0.5 }}>
                            <HelpIcon fontSize="small" color="action" />
                        </IconButton>
                    </Tooltip>
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