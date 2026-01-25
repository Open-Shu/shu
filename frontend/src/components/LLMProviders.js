import React, { useMemo, useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
  Box,
  Paper,
  Typography,
  Chip,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Alert,
  CircularProgress,
  Grid,
  Card,
  CardContent,
  CardActions
} from '@mui/material';
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  PlayArrow as TestIcon,
  Settings as SettingsIcon,
  Search as DiscoverIcon,
  Sync as SyncIcon,
  CheckBox as CheckBoxIcon,
  CheckBoxOutlineBlank as CheckBoxOutlineBlankIcon,
  Close as RemoveIcon,
  FilterList as FilterIcon
} from '@mui/icons-material';
import { llmAPI, extractDataFromResponse, formatError } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import LLMProviderForm from './shared/LLMProviderForm';
import { log } from '../utils/log';
import PageHelpHeader from './PageHelpHeader';

const createDefaultProviderCapabilities = () => ({});

const pickCapabilities = (caps, fallbackCaps) => {
  if (caps && Object.keys(caps).length > 0) return caps;
  if (fallbackCaps && Object.keys(fallbackCaps).length > 0) return fallbackCaps;
  return {};
};

const LLMProviders = () => {
  const { canManageUsers } = useAuth();
  const [editProvider, setEditProvider] = useState(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [providerToDelete, setProviderToDelete] = useState(null);
  const [testResults, setTestResults] = useState({});

  // Model management state
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState(null);
  const [discoveredModels, setDiscoveredModels] = useState([]);
  const [selectedModels, setSelectedModels] = useState(new Set());
  const [modelDiscoveryLoading, setModelDiscoveryLoading] = useState(false);
  const [modelSyncLoading, setModelSyncLoading] = useState(false);
  const [modelFilter, setModelFilter] = useState('');
  const [modelRemovalLoading, setModelRemovalLoading] = useState(new Set());

  // Manual model entry state
  const [manualModelId, setManualModelId] = useState('');
const [manualModels, setManualModels] = useState([]);

  const [newProvider, setNewProvider] = useState(() => ({
    name: '',
    provider_type: 'openai',
    api_endpoint: '',
    api_key: '',
    organization_id: '',
    is_active: true,
    provider_capabilities: createDefaultProviderCapabilities(),
    rate_limit_rpm: 0,  // 0 = unlimited
    rate_limit_tpm: 0,  // 0 = unlimited
    budget_limit_monthly: null
  }));
  const [error, setError] = useState(null);
  const queryClient = useQueryClient();

  // Fetch providers
  const { data: providersResponse, isLoading } = useQuery(
    'llm-providers',
    llmAPI.getProviders,
    {
      enabled: canManageUsers(),
      onError: (err) => {
        setError(formatError(err).message);
      }
    }
  );

  const endpointsOverrideHandler = (prev, key, field, value) => {
    return {
      ...prev,
      [key]: {
        ...(prev[key] || {}),
        [field]: value,
      },
    };
  };

  // Strip label keys from endpoint override payloads before sending to API
  const stripLabels = (val) => {
    if (Array.isArray(val)) {
      return val.map(stripLabels);
    }
    if (val && typeof val === 'object') {
      const next = {};
      Object.entries(val).forEach(([k, v]) => {
        if (k === 'label') return;
        next[k] = stripLabels(v);
      });
      return next;
    }
    return val;
  };

  // Endpoint overrides editing state
  const [endpointsOverrideEdit, setEndpointsOverrideEdit] = useState({});
  const [apiEndpointDirtyEdit, setApiEndpointDirtyEdit] = useState(false);
  const [providerCapabilitiesDirtyEdit, setProviderCapabilitiesDirtyEdit] = useState(false);
  const [apiEndpointDirtyCreate, setApiEndpointDirtyCreate] = useState(false);
  const [providerCapabilitiesDirtyCreate, setProviderCapabilitiesDirtyCreate] = useState(false);

  const providerTypeKeyForEdit = editProvider?.provider_type || null;
  const { data: providerTypeDefResp } = useQuery(
    ['llm-provider-type', providerTypeKeyForEdit],
    () => llmAPI.getProviderType(providerTypeKeyForEdit),
    { enabled: !!providerTypeKeyForEdit && editDialogOpen }
  );

  // Provider types list (for dynamic Provider Type dropdown)
  const { data: providerTypesResp } = useQuery(
    ['llm-provider-types'],
    () => llmAPI.getProviderTypes()
  );
  const providerTypes = useMemo(
    () => extractDataFromResponse(providerTypesResp) || [],
    [providerTypesResp]
  );

  const providerTypeDef = useMemo(() => extractDataFromResponse(providerTypeDefResp), [providerTypeDefResp]);

  // --- Edit dialog endpoint overrides helpers (hooks must be before any early returns)
  const baseEndpoints = useMemo(() => (providerTypeDef?.endpoints || {}), [providerTypeDef]);
  const providerCapabilities = useMemo(() => providerTypeDef?.provider_capabilities || {}, [providerTypeDef]);
  const updateEndpointField = (key, field, value) => {setEndpointsOverrideEdit((prev) => endpointsOverrideHandler(prev, key, field, value));};

  // --- Create dialog endpoint overrides helpers ---
  const [endpointsOverrideCreate, setEndpointsOverrideCreate] = useState({});
  const providerTypeKeyForCreate = newProvider.provider_type;
  const { data: providerTypeDefCreateResp } = useQuery(
    ['llm-provider-type', providerTypeKeyForCreate],
    () => llmAPI.getProviderType(providerTypeKeyForCreate),
    { enabled: !!providerTypeKeyForCreate && createDialogOpen }
  );
  const providerTypeDefCreate = useMemo(() => extractDataFromResponse(providerTypeDefCreateResp), [providerTypeDefCreateResp]);
  const baseEndpointsCreate = useMemo(() => (providerTypeDefCreate?.endpoints || {}), [providerTypeDefCreate]);
  const providerCapabilitiesCreate = useMemo(() => providerTypeDefCreate?.provider_capabilities || {}, [providerTypeDefCreate]);
  const updateEndpointFieldCreate = (key, field, value) => {setEndpointsOverrideCreate((prev) => endpointsOverrideHandler(prev, key, field, value));};

  // Auto-fill API endpoint from provider type definition if user hasn't set it
  useEffect(() => {
    if (createDialogOpen && providerTypeDefCreate?.base_url_template && !apiEndpointDirtyCreate && !newProvider.api_endpoint) {
      setNewProvider((prev) => ({ ...prev, api_endpoint: providerTypeDefCreate.base_url_template }));
    }
  }, [createDialogOpen, providerTypeDefCreate, apiEndpointDirtyCreate, newProvider.api_endpoint]);

  // Sync capability toggles for create when provider type changes
  useEffect(() => {
    if (createDialogOpen && providerCapabilitiesCreate && !providerCapabilitiesDirtyCreate) {
      setNewProvider((prev) => ({
        ...prev,
        provider_capabilities: pickCapabilities(prev.provider_capabilities, providerCapabilitiesCreate),
      }));
    }
  }, [createDialogOpen, providerCapabilitiesCreate, providerCapabilitiesDirtyCreate]);

  // Sync rate limit defaults from provider type definition when creating
  useEffect(() => {
    if (createDialogOpen && providerTypeDefCreate) {
      const rpmDefault = providerTypeDefCreate.rate_limit_rpm_default ?? 0;
      const tpmDefault = providerTypeDefCreate.rate_limit_tpm_default ?? 0;
      setNewProvider((prev) => ({
        ...prev,
        rate_limit_rpm: rpmDefault,
        rate_limit_tpm: tpmDefault,
      }));
    }
  }, [createDialogOpen, providerTypeDefCreate]);

  useEffect(() => {
    if (editDialogOpen && providerTypeDef?.base_url_template && !apiEndpointDirtyEdit && (!editProvider?.api_endpoint)) {
      setEditProvider((prev) => ({ ...prev, api_endpoint: providerTypeDef.base_url_template }));
    }
  }, [editDialogOpen, providerTypeDef, apiEndpointDirtyEdit, editProvider?.api_endpoint]);

  // Sync capability toggles for edit when provider type changes
  useEffect(() => {
    if (editDialogOpen && providerCapabilities && !providerCapabilitiesDirtyEdit) {
      setEditProvider((prev) => {
        if (!prev) return prev;
        if (prev.provider_capabilities && Object.keys(prev.provider_capabilities).length > 0) {
          return prev;
        }
        return {
          ...prev,
          provider_capabilities: providerCapabilities,
        };
      });
    }
  }, [editDialogOpen, providerCapabilities, providerCapabilitiesDirtyEdit]);

  // Clear create overrides on dialog open; will be re-initialized from base
  useEffect(() => {
    if (createDialogOpen) {
      setEndpointsOverrideCreate({});
    }
  }, [createDialogOpen]);

  // Initialize create overrides from base endpoints when available and still empty
  useEffect(() => {
    if (createDialogOpen && baseEndpointsCreate && Object.keys(baseEndpointsCreate).length > 0 && Object.keys(endpointsOverrideCreate).length === 0) {
      // Deep clone base endpoints as starting overrides
      const cloned = JSON.parse(JSON.stringify(baseEndpointsCreate));
      setEndpointsOverrideCreate(cloned);
    }
  }, [createDialogOpen, baseEndpointsCreate, endpointsOverrideCreate]);

  // Initialize edit overrides to the effective endpoints (base overlaid with existing overrides) when still empty
  useEffect(() => {
    if (editDialogOpen && baseEndpoints && Object.keys(baseEndpoints).length > 0 && editProvider && Object.keys(endpointsOverrideEdit).length === 0) {
      const existing = (editProvider.endpoints || {});
      const merged = {};
      const keys = new Set([...Object.keys(baseEndpoints), ...Object.keys(existing)]);
      keys.forEach((k) => {
        const b = baseEndpoints[k] || {};
        const o = existing[k] || {};
        merged[k] = { ...(typeof b === 'object' ? b : {}), ...(typeof o === 'object' ? o : {}) };
      });
      setEndpointsOverrideEdit(merged);
    }
  }, [editDialogOpen, baseEndpoints, editProvider, endpointsOverrideEdit]);

  // Fetch all models to show enabled counts per provider
  const { data: allModelsResponse } = useQuery(
    'all-llm-models',
    () => llmAPI.getModels(),
    {
      enabled: canManageUsers(),
      onError: (err) => {
        log.error('Error fetching models:', err);
      }
    }
  );

  // Create provider mutation
  const createProviderMutation = useMutation(
    (providerData) => llmAPI.createProvider(providerData),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('llm-providers');
        setCreateDialogOpen(false);
        resetNewProvider();
        setError(null);
      },
      onError: (err) => {
        setError(formatError(err).message);
      }
    }
  );

  // Update provider mutation
  const updateProviderMutation = useMutation(
    ({ id, data }) => llmAPI.updateProvider(id, data),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('llm-providers');
        setEditDialogOpen(false);
        setEditProvider(null);
        setError(null);
      },
      onError: (err) => {
        setError(formatError(err).message);
      }
    }
  );

  // Delete provider mutation
  const deleteProviderMutation = useMutation(
    (id) => llmAPI.deleteProvider(id),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('llm-providers');
        setDeleteDialogOpen(false);
        setProviderToDelete(null);
        setError(null);
      },
      onError: (err) => {
        setError(formatError(err).message);
      }
    }
  );

  // Test provider mutation
  const testProviderMutation = useMutation(
    (id) => llmAPI.testProvider(id),
    {
      onSuccess: (_res, id) => {
        setTestResults(prev => ({
          ...prev,
          [id]: { success: true, message: 'Connection successful' }
        }));
      },
      onError: (err, id) => {
        setTestResults(prev => ({
          ...prev,
          [id]: { success: false, message: formatError(err).message }
        }));
      }
    }
  );

  if (!canManageUsers()) {
    return (
      <Alert severity="error">
        You don't have permission to manage LLM providers.
      </Alert>
    );
  }

  const providers = extractDataFromResponse(providersResponse) || [];
  const allModels = extractDataFromResponse(allModelsResponse) || [];

  // Helper function to get enabled models for a provider
  const getProviderModels = (providerId) => {
    return allModels.filter(model => model.provider_id === providerId && model.is_active);
  };

  // Helper function to get model count for a provider
  const getProviderModelCount = (providerId) => {
    return getProviderModels(providerId).length;
  };

  // Helper function to check if a model is already enabled
  const isModelEnabled = (providerId, modelName) => {
    return allModels.some(model =>
      model.provider_id === providerId &&
      model.model_name === modelName &&
      model.is_active
    );
  };

  // Helper function to get sorted and filtered models (discovered + manual)
  const getSortedFilteredModels = () => {
    let models = [...discoveredModels, ...manualModels];

    // Filter by search term
    if (modelFilter.trim()) {
      const filterLower = modelFilter.toLowerCase();
      models = models.filter(model =>
        model.id.toLowerCase().includes(filterLower) ||
        (model.owned_by && model.owned_by.toLowerCase().includes(filterLower))
      );
    }

    // Sort by model name, with manual models marked
    models.sort((a, b) => {
      // Sort manual models to the top, then alphabetically
      if (a.manual && !b.manual) return -1;
      if (!a.manual && b.manual) return 1;
      return a.id.localeCompare(b.id);
    });

    return models;
  };

  // Helper function to get sorted enabled models
  const getSortedEnabledModels = (providerId) => {
    const models = getProviderModels(providerId);
    return models.sort((a, b) => (a.display_name || a.model_name).localeCompare(b.display_name || b.model_name));
  };

const resetNewProvider = () => {
  setNewProvider({
    name: '',
    provider_type: 'openai',
    api_endpoint: '',
      api_key: '',
      organization_id: '',
      is_active: true,
    provider_capabilities: createDefaultProviderCapabilities(),
    rate_limit_rpm: 0,  // 0 = unlimited
    rate_limit_tpm: 0,  // 0 = unlimited
    budget_limit_monthly: null,
  });
  setApiEndpointDirtyCreate(false);
  setProviderCapabilitiesDirtyCreate(false);
};

const handleEditProvider = (provider) => {
  setEditProvider({
    ...provider,
    provider_capabilities: provider.provider_capabilities && Object.keys(provider.provider_capabilities).length > 0
      ? provider.provider_capabilities
      : {},
  });
  // Let the effect initialize overrides from effective endpoints
  setEndpointsOverrideEdit({});
  setApiEndpointDirtyEdit(false);
  setProviderCapabilitiesDirtyEdit(false);
  setEditDialogOpen(true);
};


  const handleDeleteProvider = (provider) => {
    setProviderToDelete(provider);
    setDeleteDialogOpen(true);
  };

  const handleTestProvider = (providerId) => {
    testProviderMutation.mutate(providerId);
  };

  const handleSaveProvider = () => {
    if (editProvider) {
      const payload = {
        ...editProvider,
        endpoints: stripLabels(endpointsOverrideEdit),
        provider_capabilities: stripLabels(
          pickCapabilities(editProvider.provider_capabilities, providerCapabilities)
        ),
      };
      updateProviderMutation.mutate({ id: editProvider.id, data: payload });
    }
  };

  const handleCreateProvider = () => {
    const payload = {
      ...newProvider,
      endpoints: stripLabels(endpointsOverrideCreate),
      provider_capabilities: stripLabels(
        pickCapabilities(newProvider.provider_capabilities, providerCapabilitiesCreate)
      ),
    };
    createProviderMutation.mutate(payload);
  };


  const handleConfirmDelete = () => {
    if (providerToDelete) {
      deleteProviderMutation.mutate(providerToDelete.id);
    }
  };

  // Model management functions
  const handleManageModels = (provider) => {
    setSelectedProvider(provider);
    setDiscoveredModels([]);
    setSelectedModels(new Set());
    setModelFilter('');
    setModelRemovalLoading(new Set());
    setManualModelId('');
    setManualModels([]);
    setModelDialogOpen(true);
  };

  const handleDiscoverModels = async () => {
    if (!selectedProvider) return;

    setModelDiscoveryLoading(true);
    try {
      const response = await llmAPI.discoverModels(selectedProvider.id);
      const data = extractDataFromResponse(response);
      setDiscoveredModels(data.discovered_models || []);
      setError(null);
    } catch (err) {
      setError(formatError(err).message);
      setDiscoveredModels([]);
    } finally {
      setModelDiscoveryLoading(false);
    }
  };

  const handleToggleModel = (modelId) => {
    const newSelected = new Set(selectedModels);
    if (newSelected.has(modelId)) {
      newSelected.delete(modelId);
    } else {
      newSelected.add(modelId);
    }
    setSelectedModels(newSelected);
  };

  const handleSyncModels = async () => {
    if (!selectedProvider || selectedModels.size === 0) return;

    setModelSyncLoading(true);
    try {
      const selectedModelsList = Array.from(selectedModels);

      // For manual models, we need to create them individually first
      const manualModelIds = manualModels.map(model => model.id);
      const selectedManualModels = selectedModelsList.filter(modelId => manualModelIds.includes(modelId));

      // Create manual models first
      for (const modelId of selectedManualModels) {
        const manualModel = manualModels.find(model => model.id === modelId);
        if (manualModel) {
          try {
            await llmAPI.createModel(selectedProvider.id, {
              model_name: modelId,
              display_name: modelId,
              model_type: 'chat',
              is_active: true
            });
          } catch (err) {
            log.warn(`Failed to create manual model ${modelId}:`, err);
            // Continue with other models even if one fails
          }
        }
      }

      // Then sync discovered models (if any)
      const discoveredModelIds = selectedModelsList.filter(modelId => !manualModelIds.includes(modelId));
      if (discoveredModelIds.length > 0) {
        await llmAPI.syncModels(selectedProvider.id, discoveredModelIds);
      }

      // Refresh providers and models data to show updated model counts
      queryClient.invalidateQueries('llm-providers');
      queryClient.invalidateQueries('all-llm-models');

      setModelDialogOpen(false);
      setError(null);

      // Show success message
      setTestResults({
        ...testResults,
        [selectedProvider.id]: {
          success: true,
          message: `Successfully synced ${selectedModels.size} models`
        }
      });
    } catch (err) {
      setError(formatError(err).message);
    } finally {
      setModelSyncLoading(false);
    }
  };

  const handleRemoveModel = async (modelId) => {
    if (!selectedProvider) return;

    setModelRemovalLoading(prev => new Set([...prev, modelId]));
    try {
      await llmAPI.disableModel(selectedProvider.id, modelId);

      // Refresh data
      queryClient.invalidateQueries('llm-providers');
      queryClient.invalidateQueries('all-llm-models');

      setError(null);
    } catch (err) {
      setError(formatError(err).message);
    } finally {
      setModelRemovalLoading(prev => {
        const newSet = new Set(prev);
        newSet.delete(modelId);
        return newSet;
      });
    }
  };

  // Manual model entry functions
  const handleAddManualModel = () => {
    const modelId = manualModelId.trim();
    if (!modelId) return;

    // Check if model already exists in discovered or manual models
    const existsInDiscovered = discoveredModels.some(model => model.id === modelId);
    const existsInManual = manualModels.some(model => model.id === modelId);
    const isAlreadyEnabled = selectedProvider && getProviderModels(selectedProvider.id).some(model => model.model_name === modelId);

    if (existsInDiscovered || existsInManual || isAlreadyEnabled) {
      setError(`Model "${modelId}" is already in the list or enabled`);
      return;
    }

    // Add to manual models list
    const newManualModel = {
      id: modelId,
      object: 'model',
      created: Date.now() / 1000,
      owned_by: selectedProvider?.provider_type || 'manual',
      description: `Manually added model: ${modelId}`,
      manual: true
    };

    setManualModels(prev => [...prev, newManualModel]);
    setSelectedModels(prev => new Set([...prev, modelId]));
    setManualModelId('');
    setError(null);
  };

  const handleRemoveManualModel = (modelId) => {
    setManualModels(prev => prev.filter(model => model.id !== modelId));
    setSelectedModels(prev => {
      const newSet = new Set(prev);
      newSet.delete(modelId);
      return newSet;
    });
  };

  const getProviderTypeLabel = (type) => {
    const item = (providerTypes || []).find((pt) => pt.key === type);
    return item?.display_name || type;
  };

  // Default API endpoint now comes from provider type definition (base_url_template); no hard-coded mapping

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ position: 'relative' }}>
      <PageHelpHeader
        title="LLM Providers"
        description="LLM Providers connect your system to AI model services like OpenAI, Anthropic, or local Ollama instances. Configure API credentials here, then discover and enable models for use in Model Configurations."
        icon={<SettingsIcon />}
        tips={[
          'Add a provider by selecting the provider type and entering your API key/endpoint',
          'Use Test Connection to verify your credentials are working',
          'Click Manage Models to discover available models from the provider',
          'Enable specific models that you want available in Model Configurations',
          'For local models (Ollama), ensure the service is running and accessible',
        ]}
        actions={
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setCreateDialogOpen(true)}
          >
            Add Provider
          </Button>
        }
      />

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {providers.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <SettingsIcon sx={{ fontSize: 64, color: 'text.secondary', mb: 2 }} />
          <Typography variant="h6" color="text.secondary" gutterBottom>
            No LLM Providers Configured
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Add your first LLM provider to start using AI capabilities
          </Typography>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setCreateDialogOpen(true)}
          >
            Add Provider
          </Button>
        </Paper>
      ) : (
        <Grid container spacing={3}>
          {providers.map((provider) => (
            <Grid item xs={12} md={6} lg={4} key={provider.id}>
              <Card>
                <CardContent>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
                    <Typography variant="h6" component="div">
                      {provider.name}
                    </Typography>
                    <Chip
                      label={provider.is_active ? 'Active' : 'Inactive'}
                      color={provider.is_active ? 'success' : 'default'}
                      size="small"
                    />
                  </Box>

                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    <strong>Type:</strong> {getProviderTypeLabel(provider.provider_type)}
                  </Typography>

                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    <strong>Endpoint:</strong> {provider.api_endpoint}
                  </Typography>

                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    <strong>Rate Limit:</strong> {provider.rate_limit_rpm ?? 0} RPM / {provider.rate_limit_tpm?.toLocaleString() ?? '—'} TPM
                  </Typography>

                  {provider.budget_limit_monthly && (
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      <strong>Budget:</strong> ${provider.budget_limit_monthly}/month
                    </Typography>
                  )}

                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    <strong>Enabled Models:</strong> {getProviderModelCount(provider.id)}
                    {getProviderModelCount(provider.id) > 0 && (
                      <Chip
                        label={`${getProviderModelCount(provider.id)} models`}
                        size="small"
                        color="primary"
                        variant="outlined"
                        sx={{ ml: 1, fontSize: '0.7rem', height: '20px' }}
                      />
                    )}
                  </Typography>

                  {testResults[provider.id] && (
                    <Alert
                      severity={testResults[provider.id].success ? 'success' : 'error'}
                      sx={{ mt: 1, fontSize: '0.75rem' }}
                    >
                      {testResults[provider.id].message}
                    </Alert>
                  )}
                </CardContent>

                <CardActions>
                  <IconButton
                    onClick={() => handleTestProvider(provider.id)}
                    size="small"
                    color="info"
                    disabled={testProviderMutation.isLoading}
                    title="Test Connection"
                  >
                    <TestIcon />
                  </IconButton>
                  <IconButton
                    onClick={() => handleManageModels(provider)}
                    size="small"
                    color="secondary"
                    title="Manage Models"
                  >
                    <SettingsIcon />
                  </IconButton>
                  <IconButton
                    onClick={() => handleEditProvider(provider)}
                    size="small"
                    color="primary"
                    title="Edit Provider"
                  >
                    <EditIcon />
                  </IconButton>
                  <IconButton
                    onClick={() => handleDeleteProvider(provider)}
                    size="small"
                    color="error"
                    title="Delete Provider"
                  >
                    <DeleteIcon />
                  </IconButton>
                </CardActions>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {/* Create Provider Dialog */}
      <Dialog open={createDialogOpen} onClose={() => setCreateDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Add LLM Provider</DialogTitle>
        <DialogContent>
          <Box sx={{ pt: 1 }}>
            <LLMProviderForm
              provider={newProvider}
              onProviderChange={(next) => {
                if (next.api_endpoint !== newProvider.api_endpoint) setApiEndpointDirtyCreate(true);
                if (next.provider_capabilities !== newProvider.provider_capabilities) {
                  setProviderCapabilitiesDirtyCreate(true);
                }
                setNewProvider(next);
              }}
              providerTypes={providerTypes}
              onProviderTypeChange={(type) => {
                setNewProvider((prev) => ({
                  ...prev,
                  provider_type: type,
                  api_endpoint: '',
                  provider_capabilities: createDefaultProviderCapabilities(),
                }));
                setApiEndpointDirtyCreate(false);
                setEndpointsOverrideCreate({});
                setProviderCapabilitiesDirtyCreate(false);
              }}
              baseEndpoints={baseEndpointsCreate}
              providerCapabilities={providerCapabilitiesCreate}
              endpointsOverride={endpointsOverrideCreate}
              onUpdateEndpointField={(k, f, v) => updateEndpointFieldCreate(k, f, v)}
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleCreateProvider}
            variant="contained"
            disabled={createProviderMutation.isLoading || !newProvider.name || !newProvider.api_endpoint}
          >
            {createProviderMutation.isLoading ? 'Creating...' : 'Create Provider'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Edit Provider Dialog */}
      <Dialog open={editDialogOpen} onClose={() => setEditDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Edit LLM Provider</DialogTitle>
        <DialogContent>
          {editProvider && (
            <Box sx={{ pt: 1 }}>
              <LLMProviderForm
              provider={editProvider}
              onProviderChange={(next) => {
                if (next.api_endpoint !== editProvider.api_endpoint) setApiEndpointDirtyEdit(true);
                if (next.provider_capabilities !== editProvider.provider_capabilities) {
                  setProviderCapabilitiesDirtyEdit(true);
                }
                setEditProvider(next);
              }}
                providerTypes={providerTypes}
              onProviderTypeChange={(type) => {
                setEditProvider((prev) => ({
                  ...prev,
                  provider_type: type,
                  provider_capabilities: {},
                }));
                setApiEndpointDirtyEdit(false);
                setEndpointsOverrideEdit({});
                setProviderCapabilitiesDirtyEdit(false);
              }}
                baseEndpoints={baseEndpoints}
                providerCapabilities={providerCapabilities}
                endpointsOverride={endpointsOverrideEdit}
                onUpdateEndpointField={(k, f, v) => updateEndpointField(k, f, v)}
            />
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleSaveProvider}
            variant="contained"
            disabled={updateProviderMutation.isLoading}
          >
            {updateProviderMutation.isLoading ? 'Saving...' : 'Save Changes'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Provider Dialog */}
      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Delete LLM Provider</DialogTitle>
        <DialogContent>
          {providerToDelete && (
            <Box sx={{ pt: 1 }}>
              <Typography variant="body1" gutterBottom>
                Are you sure you want to delete this LLM provider?
              </Typography>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                <strong>Name:</strong> {providerToDelete.name}
              </Typography>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                <strong>Type:</strong> {getProviderTypeLabel(providerToDelete.provider_type)}
              </Typography>
              <Alert severity="warning" sx={{ mt: 2 }}>
                <strong>Warning:</strong> This action cannot be undone. All associated models and usage data will be permanently removed.
              </Alert>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleConfirmDelete}
            variant="contained"
            color="error"
            disabled={deleteProviderMutation.isLoading}
          >
            {deleteProviderMutation.isLoading ? 'Deleting...' : 'Delete Provider'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Model Management Dialog */}
      <Dialog
        open={modelDialogOpen}
        onClose={() => setModelDialogOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          Manage Models - {selectedProvider?.name}
        </DialogTitle>
        <DialogContent>
          <Box sx={{ pt: 1 }}>
            {/* Show currently enabled models */}
            {selectedProvider && getProviderModelCount(selectedProvider.id) > 0 && (
              <Box sx={{ mb: 3, p: 2, backgroundColor: 'action.hover', borderRadius: 1 }}>
                <Typography variant="h6" gutterBottom>
                  Currently Enabled Models ({getProviderModelCount(selectedProvider.id)})
                </Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                  {getSortedEnabledModels(selectedProvider.id).map((model) => (
                    <Chip
                      key={model.id}
                      label={model.display_name || model.model_name}
                      color="primary"
                      size="small"
                      variant="filled"
                      onDelete={() => handleRemoveModel(model.id)}
                      deleteIcon={
                        modelRemovalLoading.has(model.id) ? (
                          <CircularProgress size={16} color="inherit" />
                        ) : (
                          <RemoveIcon />
                        )
                      }
                      disabled={modelRemovalLoading.has(model.id)}
                    />
                  ))}
                </Box>
              </Box>
            )}

            {/* Manual Model Entry Section */}
            <Box sx={{ mb: 3, p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
              <Typography variant="h6" gutterBottom>
                Manual Model Entry
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Add a model by typing its exact ID (e.g., "claude-sonnet-4-20250514", "gpt-4o-mini")
              </Typography>
              <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
                <TextField
                  fullWidth
                  size="small"
                  label="Model ID"
                  value={manualModelId}
                  onChange={(e) => setManualModelId(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleAddManualModel();
                    }
                  }}
                  placeholder="e.g., claude-sonnet-4-20250514"
                  helperText="Press Enter or click Add to include this model"
                />
                <Button
                  variant="outlined"
                  onClick={handleAddManualModel}
                  disabled={!manualModelId.trim()}
                  sx={{ minWidth: 'auto', px: 2 }}
                >
                  Add
                </Button>
              </Box>

              {/* Show manually added models */}
              {manualModels.length > 0 && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    Manually added models:
                  </Typography>
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                    {manualModels.map((model) => (
                      <Chip
                        key={model.id}
                        label={model.id}
                        color="secondary"
                        size="small"
                        onDelete={() => handleRemoveManualModel(model.id)}
                        deleteIcon={<DeleteIcon />}
                      />
                    ))}
                  </Box>
                </Box>
              )}
            </Box>

            {discoveredModels.length === 0 && manualModels.length === 0 ? (
              <Box sx={{ textAlign: 'center', py: 4 }}>
                <Typography variant="body1" color="text.secondary" gutterBottom>
                  Discover available models from this provider's API
                </Typography>
                <Button
                  variant="contained"
                  startIcon={<DiscoverIcon />}
                  onClick={handleDiscoverModels}
                  disabled={modelDiscoveryLoading}
                  sx={{ mt: 2 }}
                >
                  {modelDiscoveryLoading ? 'Discovering...' : 'Discover Models'}
                </Button>
              </Box>
            ) : (
              <Box>
                <Typography variant="body1" gutterBottom>
                  Select models to enable for this provider:
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Found {discoveredModels.length} discovered models{manualModels.length > 0 ? ` and ${manualModels.length} manually added models` : ''}. Select the ones you want to use.
                </Typography>

                {/* Filter input */}
                <TextField
                  fullWidth
                  size="small"
                  placeholder="Filter models by name..."
                  value={modelFilter}
                  onChange={(e) => setModelFilter(e.target.value)}
                  InputProps={{
                    startAdornment: <FilterIcon sx={{ mr: 1, color: 'text.secondary' }} />
                  }}
                  sx={{ mb: 2 }}
                />

                <Box sx={{ maxHeight: 400, overflowY: 'auto' }}>
                  {getSortedFilteredModels().length === 0 && modelFilter ? (
                    <Box sx={{ textAlign: 'center', py: 4 }}>
                      <Typography variant="body2" color="text.secondary">
                        No models found matching "{modelFilter}"
                      </Typography>
                      <Button
                        size="small"
                        onClick={() => setModelFilter('')}
                        sx={{ mt: 1 }}
                      >
                        Clear Filter
                      </Button>
                    </Box>
                  ) : (
                    getSortedFilteredModels().map((model) => {
                    const isEnabled = selectedProvider && isModelEnabled(selectedProvider.id, model.id);
                    return (
                      <Box
                        key={model.id}
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          p: 1,
                          border: '1px solid',
                          borderColor: isEnabled ? 'success.main' : 'divider',
                          borderRadius: 1,
                          mb: 1,
                          cursor: isEnabled ? 'default' : 'pointer',
                          backgroundColor: isEnabled ? 'success.light' : 'transparent',
                          opacity: isEnabled ? 0.7 : 1,
                          '&:hover': {
                            backgroundColor: isEnabled ? 'success.light' : 'action.hover'
                          }
                        }}
                        onClick={() => !isEnabled && handleToggleModel(model.id)}
                      >
                        <IconButton size="small" sx={{ mr: 1 }} disabled={isEnabled}>
                          {isEnabled ? (
                            <CheckBoxIcon color="success" />
                          ) : selectedModels.has(model.id) ? (
                            <CheckBoxIcon color="primary" />
                          ) : (
                            <CheckBoxOutlineBlankIcon />
                          )}
                        </IconButton>
                        <Box sx={{ flexGrow: 1 }}>
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <Typography variant="body2" fontWeight="medium">
                              {model.id}
                            </Typography>
                            {model.manual && (
                              <Chip
                                label="Manual"
                                size="small"
                                color="secondary"
                                variant="outlined"
                                sx={{ fontSize: '0.7rem', height: '18px' }}
                              />
                            )}
                            {isEnabled && (
                              <Chip
                                label="Enabled"
                                size="small"
                                color="success"
                                variant="filled"
                                sx={{ fontSize: '0.7rem', height: '18px' }}
                              />
                            )}
                          </Box>
                          {model.owned_by && (
                            <Typography variant="caption" color="text.secondary">
                              Owned by: {model.owned_by}
                            </Typography>
                          )}
                        </Box>
                      </Box>
                    );
                  }))}
                </Box>

                <Box sx={{ mt: 2, p: 2, backgroundColor: 'action.hover', borderRadius: 1 }}>
                  <Typography variant="body2">
                    <strong>Showing:</strong> {getSortedFilteredModels().length} of {discoveredModels.length + manualModels.length} models
                    {manualModels.length > 0 && (
                      <span style={{ marginLeft: 8, color: 'text.secondary' }}>
                        ({discoveredModels.length} discovered, {manualModels.length} manual)
                      </span>
                    )}
                    {modelFilter && (
                      <span style={{ marginLeft: 8, fontStyle: 'italic' }}>
                        (filtered by "{modelFilter}")
                      </span>
                    )}
                  </Typography>
                  <Typography variant="body2">
                    <strong>Selected:</strong> {selectedModels.size} models
                  </Typography>
                </Box>
              </Box>
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setModelDialogOpen(false)}>
            Cancel
          </Button>
          {discoveredModels.length === 0 && manualModels.length === 0 ? (
            <Button
              variant="contained"
              startIcon={<DiscoverIcon />}
              onClick={handleDiscoverModels}
              disabled={modelDiscoveryLoading}
            >
              {modelDiscoveryLoading ? 'Discovering...' : 'Discover Models'}
            </Button>
          ) : (
            <>
              {discoveredModels.length === 0 && (
                <Button
                  variant="outlined"
                  startIcon={<DiscoverIcon />}
                  onClick={handleDiscoverModels}
                  disabled={modelDiscoveryLoading}
                  sx={{ mr: 1 }}
                >
                  {modelDiscoveryLoading ? 'Discovering...' : 'Discover More'}
                </Button>
              )}
              <Button
                variant="contained"
                startIcon={<SyncIcon />}
                onClick={handleSyncModels}
                disabled={modelSyncLoading || selectedModels.size === 0}
              >
                {modelSyncLoading ? 'Syncing...' : `Sync ${selectedModels.size} Models`}
              </Button>
            </>
          )}
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default LLMProviders;
