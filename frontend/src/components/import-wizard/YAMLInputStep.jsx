import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
    Box,
    Typography,
    TextField,
    Alert,
    Paper,
    Chip,
    Stack,
    Divider,
} from '@mui/material';
import {
    CheckCircle as CheckCircleIcon,
    Error as ErrorIcon,
    Info as InfoIcon,
} from '@mui/icons-material';
import YAMLProcessor from '../../services/yamlProcessor';
import { extractImportPlaceholders } from '../../services/importPlaceholders';
import { log } from '../../utils/log';

/**
 * YAMLInputStep - First step of the import wizard for YAML input and validation
 * 
 * @param {Object} props - Component props
 * @param {string} props.yamlContent - Current YAML content
 * @param {function} props.onYAMLChange - Callback when YAML content changes
 * @param {function} props.onValidationChange - Callback when validation state changes
 * @param {string} [props.prePopulatedYAML] - Pre-populated YAML content
 */
const YAMLInputStep = ({
    yamlContent,
    onYAMLChange,
    onValidationChange,
    prePopulatedYAML,
}) => {
    const [validationResult, setValidationResult] = useState({
        isValid: false,
        errors: [],
    });
    const [placeholders, setPlaceholders] = useState([]);
    const [isPrePopulated, setIsPrePopulated] = useState(false);
    const initializedRef = useRef(false);

    // Stable callback for onYAMLChange to avoid stale references
    const stableOnYAMLChange = useCallback((content) => {
        onYAMLChange(content);
    }, [onYAMLChange]);

    // Initialize with pre-populated YAML if provided
    useEffect(() => {
        if (prePopulatedYAML && !initializedRef.current) {
            setIsPrePopulated(true);
            stableOnYAMLChange(prePopulatedYAML);
            initializedRef.current = true;
            log.info('Pre-populated YAML content loaded', { length: prePopulatedYAML.length });
        }
    }, [prePopulatedYAML, stableOnYAMLChange]);

    // Validate YAML content whenever it changes
    const validateYAML = useCallback((content) => {
        if (!content || content.trim() === '') {
            const result = { isValid: false, errors: ['YAML content is required'] };
            setValidationResult(result);
            setPlaceholders([]);
            onValidationChange(false);
            return;
        }

        try {
            // Validate YAML structure
            const validation = YAMLProcessor.validateExperienceYAML(content);
            setValidationResult(validation);
            
            // Extract placeholders for display (only supported ones)
            const extractedPlaceholders = extractImportPlaceholders(content);
            setPlaceholders(extractedPlaceholders);
            
            // Notify parent of validation state
            onValidationChange(validation.isValid);
            
            if (validation.isValid) {
                log.debug('YAML validation successful', { 
                    placeholderCount: extractedPlaceholders.length 
                });
            } else {
                log.debug('YAML validation failed', { 
                    errors: validation.errors 
                });
            }
        } catch (error) {
            const result = { 
                isValid: false, 
                errors: [`Validation error: ${error.message}`] 
            };
            setValidationResult(result);
            setPlaceholders([]);
            onValidationChange(false);
            log.error('YAML validation error', { error: error.message });
        }
    }, []); // Removed onValidationChange from dependencies

    // Validate whenever YAML content changes
    useEffect(() => {
        validateYAML(yamlContent);
    }, [yamlContent, validateYAML]);

    // Handle YAML content changes
    const handleYAMLChange = (event) => {
        const newContent = event.target.value;
        setIsPrePopulated(false); // Clear pre-populated flag when user edits
        onYAMLChange(newContent);
    };

    // Get validation status icon and color
    const getValidationStatus = () => {
        if (!yamlContent || yamlContent.trim() === '') {
            return { icon: <InfoIcon />, color: 'info', text: 'Enter YAML content' };
        }
        if (validationResult.isValid) {
            return { icon: <CheckCircleIcon />, color: 'success', text: 'Valid YAML' };
        }
        return { icon: <ErrorIcon />, color: 'error', text: 'Invalid YAML' };
    };

    const status = getValidationStatus();

    return (
        <Box>
            <Typography variant="h6" gutterBottom>
                YAML Configuration
            </Typography>
            
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                Paste or edit your experience YAML configuration below. The system will validate 
                the structure and extract any placeholders that need values.
            </Typography>

            {/* Pre-populated indicator */}
            {isPrePopulated && (
                <Alert severity="info" sx={{ mb: 2 }}>
                    <Typography variant="body2">
                        This YAML has been pre-populated from the Quick Start guide. 
                        You can edit it or proceed to the next step.
                        If this is your first time setting up an experience, we recommend you leave everything as is.
                    </Typography>
                </Alert>
            )}

            {/* YAML Input Area */}
            <Paper variant="outlined" sx={{ mb: 2 }}>
                <TextField
                    multiline
                    fullWidth
                    minRows={12}
                    maxRows={20}
                    value={yamlContent}
                    onChange={handleYAMLChange}
                    placeholder="Paste your YAML configuration here..."
                    variant="outlined"
                    inputProps={{ 'aria-label': 'YAML configuration' }}
                    InputProps={{
                        sx: {
                            fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
                            fontSize: '0.875rem',
                            lineHeight: 1.5,
                            '& .MuiInputBase-input': {
                                padding: 2,
                            },
                        },
                    }}
                    sx={{
                        '& .MuiOutlinedInput-root': {
                            '& fieldset': {
                                border: 'none',
                            },
                        },
                    }}
                />
            </Paper>

            {/* Validation Status */}
            <Box sx={{ mb: 2 }}>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
                    <Box sx={{ color: `${status.color}.main` }}>
                        {status.icon}
                    </Box>
                    <Typography variant="body2" color={`${status.color}.main`}>
                        {status.text}
                    </Typography>
                    {yamlContent && (
                        <Typography variant="caption" color="text.secondary">
                            ({yamlContent.length} characters)
                        </Typography>
                    )}
                </Stack>

                {/* Validation Errors */}
                {validationResult.errors.length > 0 && (
                    <Alert severity="error" sx={{ mb: 2 }}>
                        <Typography variant="body2" fontWeight="medium" gutterBottom>
                            Validation Errors:
                        </Typography>
                        <Box component="ul" sx={{ m: 0, pl: 2 }}>
                            {validationResult.errors.map((error, index) => {
                                // Handle enhanced error objects with line/column info
                                if (typeof error === 'object' && error.type === 'yaml_parsing') {
                                    return (
                                        <Box component="li" key={index} sx={{ mb: 1 }}>
                                            <Typography variant="body2" color="error.main">
                                                <strong>Line {error.line}, Column {error.column}:</strong> {error.reason}
                                            </Typography>
                                            {error.context && (
                                                <Box 
                                                    component="pre" 
                                                    sx={{ 
                                                        mt: 1, 
                                                        p: 1, 
                                                        bgcolor: 'grey.100', 
                                                        borderRadius: 1,
                                                        fontSize: '0.75rem',
                                                        fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
                                                        overflow: 'auto',
                                                        whiteSpace: 'pre-wrap'
                                                    }}
                                                >
                                                    {error.context}
                                                </Box>
                                            )}
                                        </Box>
                                    );
                                }
                                
                                // Handle regular string errors
                                return (
                                    <Typography key={index} component="li" variant="body2">
                                        {error}
                                    </Typography>
                                );
                            })}
                        </Box>
                    </Alert>
                )}

                {/* Validation Warnings */}
                {validationResult.warnings && validationResult.warnings.length > 0 && (
                    <Alert severity="warning" sx={{ mb: 2 }}>
                        <Typography variant="body2" fontWeight="medium" gutterBottom>
                            Warnings:
                        </Typography>
                        <Box component="ul" sx={{ m: 0, pl: 2 }}>
                            {validationResult.warnings.map((warning, index) => (
                                <Typography key={index} component="li" variant="body2">
                                    {warning}
                                </Typography>
                            ))}
                        </Box>
                    </Alert>
                )}

                {/* Plugin Requirements */}
                {validationResult.pluginValidation?.requiredPlugins && 
                 Array.isArray(validationResult.pluginValidation.requiredPlugins) && 
                 validationResult.pluginValidation.requiredPlugins.length > 0 && (
                    <Alert severity="info" sx={{ mb: 2 }}>
                        <Typography variant="body2" fontWeight="medium" gutterBottom>
                            Required Plugins ({validationResult.pluginValidation.requiredPlugins.length}):
                        </Typography>
                        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
                            {validationResult.pluginValidation?.requiredPlugins?.map((plugin) => (
                                <Chip
                                    key={plugin}
                                    label={plugin}
                                    size="small"
                                    variant="outlined"
                                    color="info"
                                    icon={<InfoIcon />}
                                />
                            ))}
                        </Stack>
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                            At the moment these plugins are not installed automatically. Please install them manually to ensure the experience works as expected.
                        </Typography>
                    </Alert>
                )}

            </Box>

        </Box>
    );
};

export default YAMLInputStep;