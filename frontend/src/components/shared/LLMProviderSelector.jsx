import React, { useMemo } from 'react';
import { useQuery } from 'react-query';
import {
    FormControl,
    InputLabel,
    Select,
    MenuItem,
    Stack,
    Alert,
    FormHelperText,
} from '@mui/material';
import { llmAPI, extractDataFromResponse } from '../../services/api';

/**
 * LLMProviderSelector - Shared component for LLM provider and model selection
 * 
 * @param {Object} props - Component props
 * @param {string} props.providerId - Current provider ID
 * @param {string} props.modelName - Current model name
 * @param {function} props.onProviderChange - Callback when provider changes
 * @param {function} props.onModelChange - Callback when model changes
 * @param {Object} props.validationErrors - Validation errors object
 * @param {boolean} props.required - Whether provider is required
 * @param {boolean} props.showHelperText - Whether to show helper text
 * @param {string} props.providerLabel - Label for provider field
 * @param {string} props.modelLabel - Label for model field
 * 
 * Note: When a provider is selected, model selection becomes required
 */
const LLMProviderSelector = ({
    providerId = '',
    modelName = '',
    onProviderChange,
    onModelChange,
    validationErrors = {},
    required = false,
    showHelperText = true,
    providerLabel = 'LLM Provider',
    modelLabel = 'Model',
}) => {
    // Fetch LLM providers for selector
    const providersQuery = useQuery(
        ['llm-providers', 'list'],
        () => llmAPI.getProviders().then(extractDataFromResponse),
        { staleTime: 30000 }
    );

    // Fetch all models for the model selector
    const modelsQuery = useQuery(
        ['llm-models', 'all'],
        () => llmAPI.getModels().then(extractDataFromResponse),
        { staleTime: 30000 }
    );

    const providers = useMemo(() => {
        const items = providersQuery.data?.items || providersQuery.data || [];
        return Array.isArray(items) ? items : [];
    }, [providersQuery.data]);

    const allModels = useMemo(() => {
        const items = modelsQuery.data?.items || modelsQuery.data || [];
        return Array.isArray(items) ? items : [];
    }, [modelsQuery.data]);

    // Get active models for selected provider
    const availableModels = useMemo(() => {
        if (!providerId) return [];
        return allModels
            .filter(m => m.provider_id === providerId && m.is_active)
            .map(m => m.model_name || m.display_name);
    }, [allModels, providerId]);

    const handleProviderChange = (e) => {
        const newProviderId = e.target.value;
        if (typeof onProviderChange === 'function') {
            onProviderChange(newProviderId);
        }
        // Reset model when provider changes
        if (typeof onModelChange === 'function') {
            onModelChange('');
        }
    };

    const handleModelChange = (e) => {
        if (typeof onModelChange === 'function') {
            onModelChange(e.target.value);
        }
    };

    return (
        <Stack spacing={2}>
            <FormControl fullWidth error={!!validationErrors.llm_provider_id}>
                <InputLabel id="llm-provider-label">{providerLabel} {required ? '*' : ''}</InputLabel>
                <Select
                    id="llm-provider"
                    labelId="llm-provider-label"
                    value={providerId}
                    label={`${providerLabel} ${required ? '*' : ''}`}
                    onChange={handleProviderChange}
                >
                    <MenuItem value="">
                        <em>None</em>
                    </MenuItem>
                    {providers.map((provider) => (
                        <MenuItem key={provider.id} value={provider.id}>
                            {provider.name}
                        </MenuItem>
                    ))}
                </Select>
                {validationErrors.llm_provider_id && (
                    <FormHelperText>{validationErrors.llm_provider_id}</FormHelperText>
                )}
                {!validationErrors.llm_provider_id && showHelperText && (
                    <FormHelperText>Select an LLM provider for AI processing</FormHelperText>
                )}
            </FormControl>

            {providerId ? (
                <FormControl fullWidth error={!!validationErrors.model_name}>
                    <InputLabel id="llm-model-label">{modelLabel} *</InputLabel>
                    <Select
                        id="llm-model"
                        labelId="llm-model-label"
                        value={modelName}
                        label={`${modelLabel} *`}
                        onChange={handleModelChange}
                    >
                        <MenuItem value="">
                            <em>Default</em>
                        </MenuItem>
                        {availableModels.map((model) => (
                            <MenuItem key={model} value={model}>
                                {model}
                            </MenuItem>
                        ))}
                    </Select>
                    {validationErrors.model_name && (
                        <FormHelperText>{validationErrors.model_name}</FormHelperText>
                    )}
                    {!validationErrors.model_name && showHelperText && (
                        <FormHelperText>Choose a specific model or use provider default</FormHelperText>
                    )}
                </FormControl>
            ) : (
                showHelperText && (
                    <Alert severity="info">
                        Select an LLM provider first to choose a model.
                    </Alert>
                )
            )}
        </Stack>
    );
};

export default LLMProviderSelector;