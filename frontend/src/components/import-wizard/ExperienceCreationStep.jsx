import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
    Box,
    Typography,
    Alert,
    CircularProgress,
    Button,
    Paper,
    Stack,
    Divider,
    LinearProgress,
} from '@mui/material';
import {
    CheckCircle as CheckCircleIcon,
    Error as ErrorIcon,
    Launch as LaunchIcon,
    Refresh as RefreshIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import { experiencesAPI, extractDataFromResponse, formatError } from '../../services/api';
import YAMLProcessor from '../../services/yamlProcessor';
import { replacePlaceholders } from '../../services/importPlaceholders';
import { log } from '../../utils/log';

/**
 * ExperienceCreationStep - Final step of the import wizard showing creation progress
 * 
 * @param {Object} props - Component props
 * @param {string} props.yamlContent - The YAML content to create experience from
 * @param {Object} props.resolvedValues - Placeholder values that have been resolved
 * @param {function} props.onCreationComplete - Callback when experience is successfully created
 * @param {function} props.onRetry - Callback to retry creation (goes back to previous step)
 * @param {function} props.onClose - Callback to close the wizard (optional)
 */
const ExperienceCreationStep = ({
    yamlContent,
    resolvedValues = {},
    onCreationComplete,
    onRetry,
    onClose,
}) => {
    const [creationState, setCreationState] = useState('idle'); // 'idle', 'creating', 'success', 'error'
    const [error, setError] = useState(null);
    const [createdExperience, setCreatedExperience] = useState(null);
    const [progress, setProgress] = useState(0);
    const navigate = useNavigate();
    
    // Track if creation has been initiated to prevent double calls
    const creationInitiatedRef = useRef(false);

    // Create experience when component mounts
    useEffect(() => {
        if (creationState === 'idle' && yamlContent && !creationInitiatedRef.current) {
            creationInitiatedRef.current = true;
            createExperience();
        }
    }, [yamlContent, resolvedValues, creationState]);

    // Simulate progress during creation
    useEffect(() => {
        let progressInterval;
        
        if (creationState === 'creating') {
            setProgress(0);
            progressInterval = setInterval(() => {
                setProgress(prev => {
                    if (prev >= 90) return prev; // Stop at 90% until actual completion
                    return prev + Math.random() * 15;
                });
            }, 200);
        }

        return () => {
            if (progressInterval) {
                clearInterval(progressInterval);
            }
        };
    }, [creationState]);

    const createExperience = useCallback(async () => {
        if (!yamlContent) {
            setError('No YAML content provided');
            setCreationState('error');
            return;
        }

        setCreationState('creating');
        setError(null);
        setProgress(10);

        try {
            log.info('Starting experience creation from YAML', {
                placeholderCount: Object.keys(resolvedValues).length
            });

            // Step 1: Resolve import placeholders in YAML (only supported ones)
            setProgress(25);
            const resolvedYAML = replacePlaceholders(yamlContent, resolvedValues);
            
            // Step 2: Convert to API payload format
            setProgress(50);
            const experiencePayload = YAMLProcessor.convertToExperiencePayload(resolvedYAML);
            
            log.debug('Converted YAML to experience payload', {
                experienceName: experiencePayload.name,
                stepCount: experiencePayload.steps?.length || 0
            });

            // Step 3: Call the API to create the experience
            setProgress(75);
            const response = await experiencesAPI.create(experiencePayload);
            const createdExp = extractDataFromResponse(response);
            
            // Step 4: Success
            setProgress(100);
            setCreatedExperience(createdExp);
            setCreationState('success');
            
            log.info('Experience created successfully', {
                experienceId: createdExp.id,
                experienceName: createdExp.name
            });

            // Notify parent component
            if (onCreationComplete) {
                onCreationComplete(createdExp);
            }

        } catch (err) {
            log.error('Failed to create experience', { error: err.message });
            
            // Enhanced error handling with specific error types
            const formattedError = formatError(err);
            const errorDetails = {
                message: formattedError,
                type: 'unknown',
                canRetry: true,
                suggestions: null
            };

            // Categorize error types for better user guidance
            if (err.response?.status === 400) {
                errorDetails.type = 'validation';
                errorDetails.suggestions = [
                    'Check that all required fields are filled',
                    'Verify YAML structure matches expected format',
                    'Ensure placeholder values are valid'
                ];
            } else if (err.response?.status === 404) {
                errorDetails.type = 'not_found';
                errorDetails.suggestions = [
                    'Verify that the selected LLM provider exists',
                    'Check that the selected model is available',
                    'Ensure all referenced plugins are installed'
                ];
            } else if (err.response?.status === 422) {
                errorDetails.type = 'plugin_validation';
                errorDetails.suggestions = [
                    'Install missing plugins before creating the experience',
                    'Check plugin names and operations in the YAML',
                    'Verify plugin configurations are correct'
                ];
            } else if (err.response?.status >= 500) {
                errorDetails.type = 'server_error';
                errorDetails.suggestions = [
                    'Try again in a few moments',
                    'Check system status',
                    'Contact support if the problem persists'
                ];
            } else if (err.code === 'NETWORK_ERROR' || err.message?.includes('Network Error')) {
                errorDetails.type = 'network';
                errorDetails.suggestions = [
                    'Check your internet connection',
                    'Try again in a few moments',
                    'Verify the server is accessible'
                ];
            } else if (err.name === 'TimeoutError' || err.code === 'ECONNABORTED') {
                errorDetails.type = 'timeout';
                errorDetails.suggestions = [
                    'The request timed out - try again',
                    'Check your network connection',
                    'The server may be experiencing high load'
                ];
            }

            setError(errorDetails);
            setCreationState('error');
            setProgress(0);
        }
    }, [yamlContent, resolvedValues, onCreationComplete]);

    const handleRetry = useCallback(() => {
        if (onRetry) {
            onRetry();
        } else {
            // Reset state and try again
            creationInitiatedRef.current = false;
            setCreationState('idle');
            setError(null);
            setCreatedExperience(null);
            setProgress(0);
        }
    }, [onRetry]);

    const handleViewExperience = useCallback(() => {
        if (createdExperience?.id) {
            navigate(`/admin/experiences/${createdExperience.id}`);
        }
    }, [createdExperience, navigate]);

    const handleGoToExperiences = useCallback(() => {
        if (onClose) {
            // Close the wizard modal - user is already on experiences page
            onClose();
        } else {
            // Fallback to navigation if no close callback provided
            navigate('/admin/experiences');
        }
    }, [onClose, navigate]);

    // Render different states
    const renderContent = () => {
        switch (creationState) {
            case 'creating':
                return (
                    <Paper variant="outlined" sx={{ p: 3, textAlign: 'center' }}>
                        <CircularProgress size={48} sx={{ mb: 2 }} />
                        <Typography variant="h6" gutterBottom>
                            Creating Experience...
                        </Typography>
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                            Processing YAML configuration and setting up your experience
                        </Typography>
                        <LinearProgress 
                            variant="determinate" 
                            value={progress} 
                            sx={{ width: '100%', height: 8, borderRadius: 4 }}
                        />
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                            {Math.round(progress)}% complete
                        </Typography>
                    </Paper>
                );

            case 'success':
                return (
                    <Paper variant="outlined" sx={{ p: 3 }}>
                        <Box sx={{ textAlign: 'center', mb: 3 }}>
                            <CheckCircleIcon 
                                sx={{ fontSize: 64, color: 'success.main', mb: 2 }} 
                            />
                            <Typography variant="h5" gutterBottom color="success.main">
                                Experience Created Successfully!
                            </Typography>
                            <Typography variant="body1" color="text.secondary">
                                Your experience has been imported and is ready to use.
                            </Typography>
                        </Box>

                        <Divider sx={{ my: 3 }} />

                        {/* Experience Details */}
                        <Box sx={{ mb: 3 }}>
                            <Typography variant="h6" gutterBottom>
                                Experience Details
                            </Typography>
                            <Stack spacing={1}>
                                <Box>
                                    <Typography variant="body2" color="text.secondary">
                                        Name:
                                    </Typography>
                                    <Typography variant="body1" fontWeight="medium">
                                        {createdExperience?.name}
                                    </Typography>
                                </Box>
                                <Box>
                                    <Typography variant="body2" color="text.secondary">
                                        Description:
                                    </Typography>
                                    <Typography variant="body1">
                                        {createdExperience?.description}
                                    </Typography>
                                </Box>
                                <Box>
                                    <Typography variant="body2" color="text.secondary">
                                        Steps:
                                    </Typography>
                                    <Typography variant="body1">
                                        {createdExperience?.step_count || 0} configured
                                    </Typography>
                                </Box>
                                <Box>
                                    <Typography variant="body2" color="text.secondary">
                                        Trigger:
                                    </Typography>
                                    <Typography variant="body1">
                                        {createdExperience?.trigger_type === 'cron' 
                                            ? 'Recurring' 
                                            : createdExperience?.trigger_type === 'scheduled'
                                            ? 'Scheduled'
                                            : 'Manual'}
                                    </Typography>
                                </Box>
                            </Stack>
                        </Box>

                        {/* Action Buttons */}
                        <Stack direction="row" spacing={2} justifyContent="center">
                            <Button
                                variant="contained"
                                startIcon={<LaunchIcon />}
                                onClick={handleViewExperience}
                                size="large"
                            >
                                View Experience
                            </Button>
                            <Button
                                variant="outlined"
                                onClick={handleGoToExperiences}
                            >
                                Go to Experiences
                            </Button>
                        </Stack>
                    </Paper>
                );

            case 'error':
                return (
                    <Paper variant="outlined" sx={{ p: 3 }}>
                        <Box sx={{ textAlign: 'center', mb: 3 }}>
                            <ErrorIcon 
                                sx={{ fontSize: 64, color: 'error.main', mb: 2 }} 
                            />
                            <Typography variant="h5" gutterBottom color="error.main">
                                Creation Failed
                            </Typography>
                            <Typography variant="body1" color="text.secondary">
                                There was an error creating your experience.
                            </Typography>
                        </Box>

                        <Alert severity="error" sx={{ mb: 3 }}>
                            <Typography variant="body2" fontWeight="medium" gutterBottom>
                                Error Details:
                            </Typography>
                            <Typography variant="body2">
                                {typeof error === 'object' && error ? error.message : error}
                            </Typography>
                        </Alert>

                        {/* Enhanced error guidance based on error type */}
                        {typeof error === 'object' && error && error.suggestions && error.suggestions.length > 0 && (
                            <Alert severity="info" sx={{ mb: 3 }}>
                                <Typography variant="body2" fontWeight="medium" gutterBottom>
                                    {error.type === 'network' ? 'Network Issues:' :
                                     error.type === 'validation' ? 'Validation Issues:' :
                                     error.type === 'plugin_validation' ? 'Plugin Issues:' :
                                     error.type === 'server_error' ? 'Server Issues:' :
                                     error.type === 'timeout' ? 'Timeout Issues:' :
                                     'Troubleshooting Tips:'}
                                </Typography>
                                <Box component="ul" sx={{ m: 0, pl: 2 }}>
                                    {error.suggestions.map((suggestion, index) => (
                                        <Typography key={index} component="li" variant="body2">
                                            {suggestion}
                                        </Typography>
                                    ))}
                                </Box>
                            </Alert>
                        )}

                        {/* General troubleshooting tips for non-enhanced errors */}
                        {(typeof error !== 'object' || !error || !error.suggestions || error.suggestions.length === 0) && (
                            <Alert severity="info" sx={{ mb: 3 }}>
                                <Typography variant="body2" fontWeight="medium" gutterBottom>
                                    Troubleshooting Tips:
                                </Typography>
                                <Box component="ul" sx={{ m: 0, pl: 2 }}>
                                    <Typography component="li" variant="body2">
                                        Check that all required plugins are installed and enabled
                                    </Typography>
                                    <Typography component="li" variant="body2">
                                        Verify that your LLM provider and model selections are valid
                                    </Typography>
                                    <Typography component="li" variant="body2">
                                        Ensure all placeholder values are properly filled
                                    </Typography>
                                    <Typography component="li" variant="body2">
                                        Review the YAML structure for any syntax errors
                                    </Typography>
                                    <Typography component="li" variant="body2">
                                        Check your network connection and try again
                                    </Typography>
                                </Box>
                            </Alert>
                        )}

                        {/* Action Buttons */}
                        <Stack direction="row" spacing={2} justifyContent="center">
                            <Button
                                variant="contained"
                                startIcon={<RefreshIcon />}
                                onClick={handleRetry}
                                size="large"
                                disabled={typeof error === 'object' && !error.canRetry}
                            >
                                {typeof error === 'object' && error.type === 'network' ? 'Retry Connection' :
                                 typeof error === 'object' && error.type === 'timeout' ? 'Try Again' :
                                 'Try Again'}
                            </Button>
                            <Button
                                variant="outlined"
                                onClick={handleGoToExperiences}
                            >
                                Cancel Import
                            </Button>
                        </Stack>
                    </Paper>
                );

            default:
                return (
                    <Paper variant="outlined" sx={{ p: 3, textAlign: 'center' }}>
                        <Typography variant="body1" color="text.secondary">
                            Preparing to create experience...
                        </Typography>
                    </Paper>
                );
        }
    };

    return (
        <Box>
            <Typography variant="h6" gutterBottom>
                Create Experience
            </Typography>
            
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                Creating your experience from the configured YAML. This may take a few moments.
            </Typography>

            {renderContent()}

        </Box>
    );
};

export default ExperienceCreationStep;
