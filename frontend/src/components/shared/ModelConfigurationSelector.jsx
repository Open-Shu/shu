import { useMemo } from 'react';
import { useQuery } from 'react-query';
import {
    FormControl,
    InputLabel,
    Select,
    MenuItem,
    Stack,
    Alert,
    FormHelperText,
    Card,
    CardContent,
    Typography,
    Box,
    Chip,
} from '@mui/material';
import {
    SmartToy as ModelIcon,
    Psychology as PromptIcon,
} from '@mui/icons-material';
import { modelConfigAPI, extractDataFromResponse } from '../../services/api';

/**
 * ModelConfigurationSelector - Component for selecting model configurations
 * 
 * @param {Object} props - Component props
 * @param {string} props.modelConfigurationId - Current model configuration ID
 * @param {function} props.onModelConfigurationChange - Callback when model configuration changes
 * @param {Object} props.validationErrors - Validation errors object
 * @param {boolean} props.required - Whether model configuration is required
 * @param {boolean} props.showHelperText - Whether to show helper text
 * @param {string} props.label - Label for the field
 * @param {boolean} props.showDetails - Whether to show configuration details when selected
 */
const ModelConfigurationSelector = ({
    modelConfigurationId = '',
    onModelConfigurationChange,
    validationErrors = {},
    required = false,
    showHelperText = true,
    label = 'Model Configuration',
    showDetails = true,
}) => {
    // Fetch model configurations for selector
    const modelConfigsQuery = useQuery(
        ['model-configurations', 'active'],
        () => modelConfigAPI.list({ 
            is_active: true, 
            include_relationships: true 
        }).then(extractDataFromResponse),
        { staleTime: 30000 }
    );

    const modelConfigurations = useMemo(() => {
        const items = modelConfigsQuery.data?.items || modelConfigsQuery.data || [];
        return Array.isArray(items) ? items : [];
    }, [modelConfigsQuery.data]);

    // Find the selected configuration for details display
    const selectedConfiguration = useMemo(() => {
        if (!modelConfigurationId) return null;
        return modelConfigurations.find(config => config.id === modelConfigurationId) || null;
    }, [modelConfigurations, modelConfigurationId]);

    const handleModelConfigurationChange = (e) => {
        const newConfigId = e.target.value;
        if (typeof onModelConfigurationChange === 'function') {
            onModelConfigurationChange(newConfigId);
        }
    };

    if (modelConfigsQuery.isError) {
        return (
            <Alert severity="error">
                Failed to load model configurations. Please try again.
            </Alert>
        );
    }

    return (
        <Stack spacing={2}>
            <FormControl fullWidth error={!!validationErrors.model_configuration_id}>
                <InputLabel id="model-configuration-label">
                    {label} {required ? '*' : ''}
                </InputLabel>
                <Select
                    id="model-configuration"
                    labelId="model-configuration-label"
                    value={modelConfigurationId}
                    label={`${label} ${required ? '*' : ''}`}
                    onChange={handleModelConfigurationChange}
                    disabled={modelConfigsQuery.isLoading}
                >
                    <MenuItem value="">
                        <em>No LLM Synthesis</em>
                    </MenuItem>
                    {modelConfigurations.map((config) => (
                        <MenuItem key={config.id} value={config.id}>
                            <Box>
                                <Typography variant="body1">{config.name}</Typography>
                                <Typography variant="caption" color="textSecondary">
                                    {config.llm_provider?.name} - {config.model_name}
                                </Typography>
                            </Box>
                        </MenuItem>
                    ))}
                </Select>
                {validationErrors.model_configuration_id && (
                    <FormHelperText>{validationErrors.model_configuration_id}</FormHelperText>
                )}
                {!validationErrors.model_configuration_id && showHelperText && (
                    <FormHelperText>
                        Select a model configuration for AI processing, or leave empty for no LLM synthesis
                    </FormHelperText>
                )}
            </FormControl>

            {/* Show model configuration details when selected */}
            {showDetails && selectedConfiguration && (
                <Card variant="outlined" sx={{ mt: 2 }}>
                    <CardContent sx={{ pb: 2 }}>
                        <Typography variant="h6" gutterBottom>
                            Selected Configuration
                        </Typography>
                        
                        {selectedConfiguration.description && (
                            <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
                                {selectedConfiguration.description}
                            </Typography>
                        )}
                        
                        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 1 }}>
                            <Chip
                                icon={<ModelIcon />}
                                label={`${selectedConfiguration.llm_provider?.name || 'Unknown'} - ${selectedConfiguration.model_name}`}
                                size="small"
                                variant="outlined"
                            />
                            
                            {selectedConfiguration.prompt && (
                                <Chip
                                    icon={<PromptIcon />}
                                    label={selectedConfiguration.prompt.name}
                                    size="small"
                                    variant="outlined"
                                    color="secondary"
                                />
                            )}
                        </Box>

                        {/* Show prompt usage information */}
                        {selectedConfiguration.prompt && (
                            <Box sx={{ mt: 1 }}>
                                <Typography variant="caption" color="textSecondary" display="block">
                                    Default Prompt: {selectedConfiguration.prompt.name}
                                </Typography>
                                <Typography variant="body2" color="textSecondary" sx={{ fontStyle: 'italic' }}>
                                    Used only when no experience prompt is provided
                                </Typography>
                            </Box>
                        )}

                        {/* Show knowledge bases information if configured */}
                        {selectedConfiguration.knowledge_bases && 
                         selectedConfiguration.knowledge_bases.length > 0 && (
                            <Box sx={{ mt: 1 }}>
                                <Typography variant="caption" color="textSecondary" display="block">
                                    Knowledge Bases ({selectedConfiguration.knowledge_bases.length}):
                                </Typography>
                                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5, mb: 1 }}>
                                    {selectedConfiguration.knowledge_bases.map((kb) => (
                                        <Chip
                                            key={kb.id}
                                            label={kb.name}
                                            size="small"
                                            variant="outlined"
                                            color="info"
                                        />
                                    ))}
                                </Box>
                                <Alert severity="info" sx={{ mt: 1 }}>
                                    <Typography variant="body2">
                                        These knowledge bases are configured for this model but are not automatically queried. 
                                        To use them, add specific knowledge base query steps to your experience workflow below.
                                    </Typography>
                                </Alert>
                            </Box>
                        )}

                        {/* Show parameter overrides if available */}
                        {selectedConfiguration.parameter_overrides && 
                         Object.keys(selectedConfiguration.parameter_overrides).length > 0 && (
                            <Box sx={{ mt: 1 }}>
                                <Typography variant="caption" color="textSecondary" display="block">
                                    Parameter Overrides:
                                </Typography>
                                <Typography variant="body2" color="textSecondary">
                                    {Object.keys(selectedConfiguration.parameter_overrides).join(', ')}
                                </Typography>
                            </Box>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Show loading state */}
            {modelConfigsQuery.isLoading && showHelperText && (
                <Alert severity="info">
                    Loading model configurations...
                </Alert>
            )}

            {/* Show empty state */}
            {!modelConfigsQuery.isLoading && 
             modelConfigurations.length === 0 && 
             showHelperText && (
                <Alert severity="warning">
                    No active model configurations found. Create one in the Model Configurations section first.
                </Alert>
            )}
        </Stack>
    );
};

export default ModelConfigurationSelector;