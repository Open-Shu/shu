import { log } from '../utils/log';

import { useState, useMemo } from 'react';
import {
  Box,
  Typography,
  Button,
  Card,
  CardContent,
  Grid,
  Chip,
  IconButton,
  Alert,
  Paper,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Divider,
  CircularProgress,
} from '@mui/material';
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  PlayArrow as TestIcon,
  Settings as SettingsIcon,
  SmartToy as ModelIcon,
  Psychology as PromptIcon,
  Storage as KnowledgeBaseIcon,
  Refresh as RefreshIcon,
  Call as CallIcon,
} from '@mui/icons-material';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
  modelConfigAPI,
  llmAPI,
  knowledgeBaseAPI,
  extractDataFromResponse,
  extractItemsFromResponse,
  formatError,
} from '../services/api';
import { promptAPI } from '../api/prompts';
import { useAuth } from '../hooks/useAuth';
import ModelConfigurationDialog from './shared/ModelConfigurationDialog';
import { sideCallsAPI } from '../services/api';
import PageHelpHeader from './PageHelpHeader';
import TuneIcon from '@mui/icons-material/Tune';

const ModelConfigurations = () => {
  const { canManagePromptsAndModels, handleAuthError } = useAuth();
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [testDialogOpen, setTestDialogOpen] = useState(false);
  const [selectedConfig, setSelectedConfig] = useState(null);
  // Store original config data for rollback when edit is cancelled without successful test
  const [originalConfigData, setOriginalConfigData] = useState(null);
  const [configToDelete, setConfigToDelete] = useState(null);
  const [testMessage, setTestMessage] = useState('Hello, how are you?');
  const [testResults, setTestResults] = useState({});
  const [error, setError] = useState(null);
  const [submitError, setSubmitError] = useState(null);
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    llm_provider_id: '',
    model_name: '',
    prompt_id: '',
    knowledge_base_ids: [],
    kb_prompt_assignments: [],
    is_active: true,
    functionalities: {},
    is_side_call_model: false,
    is_profiling_model: false,
  });

  // Advanced parameters state
  const [paramOverrides, setParamOverrides] = useState({});
  const [advancedJson, setAdvancedJson] = useState('');
  const [advancedJsonError, setAdvancedJsonError] = useState(null);

  const queryClient = useQueryClient();

  // Fetch model configurations
  const { data: configurations = [], isLoading: configsLoading } = useQuery(
    ['model-configurations', { includeInactive: true }],
    () => modelConfigAPI.list({ include_relationships: true, active_only: false }),
    {
      enabled: canManagePromptsAndModels(),
      select: extractItemsFromResponse,
      onError: (err) => {
        if (err.response?.status === 401) {
          handleAuthError();
        } else {
          setError(formatError(err).message);
        }
      },
    }
  );

  // Fetch current side-call model configuration (default)
  const { data: sideCallConfig = null } = useQuery('side-call-config', () => sideCallsAPI.getConfig('default'), {
    enabled: canManagePromptsAndModels(),
    select: (data) => data ?? null,
    onError: (err) => {
      if (err.response?.status === 401) {
        handleAuthError();
      } else {
        log.error('ModelConfigurations - Failed to fetch side-call config:', err);
      }
    },
  });

  // Fetch profiling model configuration (separate from default side-call)
  const { data: profilingConfig = null } = useQuery('profiling-config', () => sideCallsAPI.getConfig('profiling'), {
    enabled: canManagePromptsAndModels(),
    select: (data) => data ?? null,
    onError: (err) => {
      if (err.response?.status === 401) {
        handleAuthError();
      } else {
        log.error('ModelConfigurations - Failed to fetch profiling config:', err);
      }
    },
  });

  // Fetch LLM providers for form
  const { data: providers = [] } = useQuery('llm-providers', llmAPI.getProviders, {
    enabled: canManagePromptsAndModels(),
    select: (response) => {
      const data = extractDataFromResponse(response);
      return Array.isArray(data) ? data : [];
    },
    onError: (err) => {
      if (err.response?.status === 401) {
        handleAuthError();
      }
    },
  });

  // Fetch models for selected provider
  const { data: models = [] } = useQuery(
    ['llm-models', formData.llm_provider_id],
    () => llmAPI.getModels(formData.llm_provider_id),
    {
      enabled: canManagePromptsAndModels() && !!formData.llm_provider_id,
      select: (response) => {
        const data = extractDataFromResponse(response);
        return Array.isArray(data) ? data : [];
      },
      onError: (err) => {
        if (err.response?.status === 401) {
          handleAuthError();
        }
      },
    }
  );

  // Fetch prompts for form (both LLM model and knowledge base prompts)
  const { data: prompts = [], isLoading: promptsLoading } = useQuery(
    'prompts-for-model-configs', // Changed key to force cache refresh
    () => promptAPI.list({ limit: 100 }), // Fetch all prompts, filter in UI
    {
      enabled: canManagePromptsAndModels(),
      select: (response) => extractItemsFromResponse(response),
      onError: (err) => {
        log.error('Error loading prompts:', err);
        if (err.response?.status === 401) {
          handleAuthError();
        }
      },
      onSuccess: (data) => {
        log.info('Prompts loaded successfully:', data);
      },
    }
  );

  // Fetch knowledge bases for form
  const { data: knowledgeBases = [], isLoading: knowledgeBasesLoading } = useQuery(
    'knowledge-bases-for-models',
    knowledgeBaseAPI.list,
    {
      enabled: canManagePromptsAndModels(),
      select: extractItemsFromResponse,
      onError: (err) => {
        if (err.response?.status === 401) {
          handleAuthError();
        }
      },
    }
  );

  // Create mutation
  const createMutation = useMutation((data) => modelConfigAPI.create(data), {
    onSuccess: (response, variables) => {
      log.info('ModelConfigurations - Create success:', response);
      queryClient.invalidateQueries(['model-configurations', { includeInactive: true }]);
      // Invalidate side-call config query if this model is marked for side calls
      if (variables.is_side_call_model) {
        queryClient.invalidateQueries('side-call-config');
      }
      // Invalidate profiling config query if this model is marked for profiling
      if (variables.is_profiling_model) {
        queryClient.invalidateQueries('profiling-config');
      }
      setCreateDialogOpen(false);
      resetForm();
      setError(null);
      setSubmitError(null);
    },
    onError: (err) => {
      log.error('ModelConfigurations - Create error:', err);
      const serverMsg = err?.response?.data?.error?.message || formatError(err).message;
      setSubmitError(serverMsg);
    },
  });

  // Update mutation
  const updateMutation = useMutation(({ id, data }) => modelConfigAPI.update(id, data), {
    onSuccess: (response, variables) => {
      queryClient.invalidateQueries(['model-configurations', { includeInactive: true }]);

      const payload = variables?.data || {};
      const isSideCallFlagProvided = Object.prototype.hasOwnProperty.call(payload, 'is_side_call_model');
      const isCurrentlySideCallModel =
        sideCallConfig?.side_call_model_config?.id && sideCallConfig.side_call_model_config.id === variables?.id;

      if (isSideCallFlagProvided || isCurrentlySideCallModel) {
        queryClient.invalidateQueries('side-call-config');
      }

      // Handle profiling model changes
      const isProfilingFlagProvided = Object.prototype.hasOwnProperty.call(payload, 'is_profiling_model');
      const isCurrentlyProfilingModel =
        profilingConfig?.side_call_model_config?.id && profilingConfig.side_call_model_config.id === variables?.id;

      if (isProfilingFlagProvided || isCurrentlyProfilingModel) {
        queryClient.invalidateQueries('profiling-config');
      }

      setEditDialogOpen(false);
      setSelectedConfig(null);
      resetForm();
      setError(null);
      setSubmitError(null);
    },
    onError: (err) => {
      const serverMsg = err?.response?.data?.error?.message || formatError(err).message;
      setSubmitError(serverMsg);
    },
  });

  // Delete mutation
  const deleteMutation = useMutation((id) => modelConfigAPI.delete(id), {
    onSuccess: () => {
      queryClient.invalidateQueries(['model-configurations', { includeInactive: true }]);
      setDeleteDialogOpen(false);
      setConfigToDelete(null);
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  // Test mutation
  const testMutation = useMutation(
    ({ id, message }) => {
      const formData = new FormData();
      formData.append('test_message', message);
      formData.append('include_knowledge_bases', 'true');
      return modelConfigAPI.testWithFile(id, formData);
    },
    {
      onSuccess: (response, { id }) => {
        const result = extractDataFromResponse(response);
        setTestResults((prev) => ({
          ...prev,
          [id]: { success: true, ...result },
        }));
      },
      onError: (err, { id }) => {
        setTestResults((prev) => ({
          ...prev,
          [id]: { success: false, error: formatError(err) },
        }));
      },
    }
  );

  const sideCallConfigId = sideCallConfig?.side_call_model_config?.id ?? null;
  const profilingConfigId = profilingConfig?.side_call_model_config?.id ?? null;

  // Debug logging for prompts
  log.debug('ModelConfigurations - prompts:', prompts);
  log.debug('ModelConfigurations - promptsLoading:', promptsLoading);
  if (prompts.length > 0) {
    log.debug('ModelConfigurations - First prompt object:', JSON.stringify(prompts[0], null, 2));
  }
  log.debug(
    'ModelConfigurations - LLM prompts:',
    prompts.filter((p) => p.entity_type === 'llm_model')
  );
  log.debug(
    'ModelConfigurations - KB prompts:',
    prompts.filter((p) => p.entity_type === 'knowledge_base')
  );

  // Resolve provider type definition for typed parameter mapping
  const selectedProvider = providers.find((p) => p.id === formData.llm_provider_id);
  const providerTypeKey = selectedProvider?.provider_type || null;
  const { data: providerTypeDef = null } = useQuery(
    ['llm-provider-type', providerTypeKey],
    () => llmAPI.getProviderType(providerTypeKey),
    {
      enabled: canManagePromptsAndModels() && !!providerTypeKey,
      select: (response) => extractDataFromResponse(response) ?? null,
      onError: (err) => {
        if (err.response?.status === 401) {
          handleAuthError();
        }
      },
    }
  );
  const parameterMapping = useMemo(() => providerTypeDef?.parameter_mapping || {}, [providerTypeDef]);
  const visibleParams = useMemo(
    () => Object.entries(parameterMapping).filter(([, spec]) => spec && spec.type !== 'hidden'),
    [parameterMapping]
  );

  if (!canManagePromptsAndModels()) {
    return <Alert severity="error">You don't have permission to manage model configurations.</Alert>;
  }

  // Show authentication error if present
  if (error && error.includes('Authentication')) {
    return (
      <Alert severity="error">
        Authentication failed. Please <a href="/auth">log in again</a>.
      </Alert>
    );
  }

  // Show loading state while essential data is loading
  if (promptsLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
        <CircularProgress />
      </Box>
    );
  }

  const resetForm = () => {
    setFormData({
      name: '',
      description: '',
      llm_provider_id: '',
      model_name: '',
      prompt_id: null,
      knowledge_base_ids: [],
      kb_prompt_assignments: [],
      is_active: true,
      functionalities: {},
      is_side_call_model: false,
      is_profiling_model: false,
    });
  };

  const handleEdit = (config) => {
    setSelectedConfig(config);

    // Handle prompt_id - ensure it's either a valid string or null
    let promptId = config.prompt_id;
    if (promptId === '' || promptId === undefined) {
      promptId = null;
    }

    // Extract knowledge base IDs
    const kbIds = config.knowledge_bases?.map((kb) => kb.id) || [];

    // Extract KB prompt assignments
    const kbPromptAssignments = [];
    if (config.kb_prompts) {
      Object.entries(config.kb_prompts).forEach(([kbId, promptInfo]) => {
        kbPromptAssignments.push({
          knowledge_base_id: kbId,
          prompt_id: promptInfo.id,
        });
      });
    }

    const newFormData = {
      name: config.name,
      description: config.description || '',
      llm_provider_id: config.llm_provider_id,
      model_name: config.model_name,
      prompt_id: promptId,
      knowledge_base_ids: kbIds,
      kb_prompt_assignments: kbPromptAssignments,
      is_active: config.is_active,
      functionalities: config.functionalities,
      is_side_call_model: config.id === sideCallConfigId,
      is_profiling_model: config.id === profilingConfigId,
    };

    setFormData(newFormData);

    // Store original config data for rollback if user cancels without successful test
    // This is the payload format needed for the update API
    const existingOverrides = config.parameter_overrides || {};
    setOriginalConfigData({
      ...newFormData,
      parameter_overrides: existingOverrides,
    });

    // Initialize advanced parameter editors from existing overrides (if any)
    setParamOverrides(existingOverrides);
    try {
      setAdvancedJson(JSON.stringify(existingOverrides, null, 2));
      setAdvancedJsonError(null);
    } catch {
      setAdvancedJson('');
    }
    setEditDialogOpen(true);
  };

  const handleDelete = (config) => {
    setConfigToDelete(config);
    setDeleteDialogOpen(true);
  };

  const handleConfirmDelete = () => {
    if (configToDelete) {
      deleteMutation.mutate(configToDelete.id);
    }
  };

  const handleTest = (config) => {
    setSelectedConfig(config);
    setTestDialogOpen(true);
  };

  const handleRunTest = () => {
    if (selectedConfig) {
      testMutation.mutate({
        id: selectedConfig.id,
        message: testMessage,
      });
    }
  };

  return (
    <Box>
      <PageHelpHeader
        title="Model Configurations"
        description="Model Configurations combine an LLM provider, model, system prompt, and optional knowledge bases into a usable AI configuration. This is where you define how your assistant behaves."
        icon={<TuneIcon />}
        tips={[
          'Create at least one Model Configuration to enable the chat interface',
          'Each configuration links a provider (e.g., OpenAI, Anthropic) with a specific model',
          "Attach a system prompt to define the assistant's personality and behavior",
          'Add Knowledge Bases to enable RAG—the assistant will search them for context',
          'Use parameter overrides to tune temperature, max tokens, and other model settings',
        ]}
        actions={
          <Box display="flex" gap={1}>
            <Button
              variant="outlined"
              startIcon={<RefreshIcon />}
              onClick={() => queryClient.invalidateQueries(['model-configurations', { includeInactive: true }])}
              disabled={configsLoading}
            >
              Refresh
            </Button>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={() => {
                resetForm();
                setParamOverrides({});
                setAdvancedJson('');
                setAdvancedJsonError(null);
                setSubmitError(null);
                setCreateDialogOpen(true);
              }}
            >
              Create Configuration
            </Button>
          </Box>
        }
      />

      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {configsLoading ? (
        <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
          <CircularProgress />
        </Box>
      ) : configurations.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <SettingsIcon sx={{ fontSize: 64, color: 'text.secondary', mb: 2 }} />
          <Typography variant="h6" color="text.secondary" gutterBottom>
            No Model Configurations
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Create your first model configuration to combine models, prompts, and knowledge bases
          </Typography>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => {
              resetForm();
              setParamOverrides({});
              setAdvancedJson('');
              setAdvancedJsonError(null);
              setSubmitError(null);
              setCreateDialogOpen(true);
            }}
          >
            Create Configuration
          </Button>
        </Paper>
      ) : (
        <Grid container spacing={3}>
          {configurations.map((config) => {
            // Mark if this is the current side-call or profiling model
            const isSideCallModel = config.id === sideCallConfigId;
            const isProfilingModel = config.id === profilingConfigId;

            return (
              <Grid item xs={12} md={6} lg={4} key={config.id}>
                <Card>
                  <CardContent>
                    <Box
                      sx={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'flex-start',
                        mb: 2,
                      }}
                    >
                      <Typography variant="h6" component="div">
                        {config.name}
                      </Typography>
                      <Box>
                        <IconButton size="small" onClick={() => handleTest(config)} title="Test Configuration">
                          <TestIcon />
                        </IconButton>
                        <IconButton size="small" onClick={() => handleEdit(config)} title="Edit Configuration">
                          <EditIcon />
                        </IconButton>
                        <IconButton
                          size="small"
                          onClick={() => handleDelete(config)}
                          title="Delete Configuration"
                          color="error"
                        >
                          <DeleteIcon />
                        </IconButton>
                      </Box>
                    </Box>

                    {config.description && (
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                        {config.description}
                      </Typography>
                    )}

                    <Box sx={{ mb: 2 }}>
                      <Chip
                        icon={<ModelIcon />}
                        label={`${config.llm_provider?.name || 'Unknown'} / ${config.model_name}`}
                        size="small"
                        sx={{ mr: 1, mb: 1 }}
                      />
                      {config.prompt && (
                        <Chip icon={<PromptIcon />} label={config.prompt.name} size="small" sx={{ mr: 1, mb: 1 }} />
                      )}
                      {config.knowledge_bases?.length > 0 && (
                        <Chip
                          icon={<KnowledgeBaseIcon />}
                          label={`${config.knowledge_bases.length} KB${config.knowledge_bases.length > 1 ? 's' : ''}`}
                          size="small"
                          sx={{ mr: 1, mb: 1 }}
                        />
                      )}
                      {config.kb_prompts && Object.keys(config.kb_prompts).length > 0 && (
                        <Chip
                          icon={<PromptIcon />}
                          label={`${Object.keys(config.kb_prompts).length} KB Prompt${Object.keys(config.kb_prompts).length > 1 ? 's' : ''}`}
                          size="small"
                          color="secondary"
                          sx={{ mr: 1, mb: 1 }}
                        />
                      )}
                    </Box>

                    <Box
                      sx={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                        <Chip
                          label={config.is_active ? 'Active' : 'Inactive'}
                          color={config.is_active ? 'success' : 'default'}
                          size="small"
                        />
                        {isSideCallModel && <Chip icon={<CallIcon />} label="Side Call" color="info" size="small" />}
                        {isProfilingModel && (
                          <Chip icon={<SettingsIcon />} label="Profiling" color="secondary" size="small" />
                        )}
                      </Box>
                      {testResults[config.id] && (
                        <Chip
                          label={testResults[config.id].success ? 'Test Passed' : 'Test Failed'}
                          color={testResults[config.id].success ? 'success' : 'error'}
                          size="small"
                        />
                      )}
                    </Box>
                  </CardContent>
                </Card>
              </Grid>
            );
          })}
        </Grid>
      )}

      {/* Create Dialog */}
      <ModelConfigurationDialog
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        title="Create Model Configuration"
        formData={formData}
        setFormData={setFormData}
        providers={providers}
        models={models}
        prompts={prompts}
        knowledgeBases={knowledgeBases}
        knowledgeBasesLoading={knowledgeBasesLoading}
        promptsLoading={promptsLoading}
        visibleParams={visibleParams}
        paramOverrides={paramOverrides}
        setParamOverrides={setParamOverrides}
        advancedJson={advancedJson}
        setAdvancedJson={setAdvancedJson}
        advancedJsonError={advancedJsonError}
        setAdvancedJsonError={setAdvancedJsonError}
        submitError={submitError}
        isSubmitting={createMutation.isLoading}
        isEditMode={false}
        existingConfigId={null}
      />

      {/* Edit Dialog */}
      <ModelConfigurationDialog
        open={editDialogOpen}
        onClose={() => setEditDialogOpen(false)}
        title="Edit Model Configuration"
        formData={formData}
        setFormData={setFormData}
        providers={providers}
        models={models}
        prompts={prompts}
        knowledgeBases={knowledgeBases}
        knowledgeBasesLoading={knowledgeBasesLoading}
        promptsLoading={promptsLoading}
        visibleParams={visibleParams}
        paramOverrides={paramOverrides}
        setParamOverrides={setParamOverrides}
        advancedJson={advancedJson}
        setAdvancedJson={setAdvancedJson}
        advancedJsonError={advancedJsonError}
        setAdvancedJsonError={setAdvancedJsonError}
        submitError={submitError}
        isSubmitting={updateMutation.isLoading}
        isEditMode={true}
        existingConfigId={selectedConfig?.id}
        originalConfigData={originalConfigData}
      />

      {/* Delete Dialog */}
      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Delete Model Configuration</DialogTitle>
        <DialogContent>
          <Typography>
            Are you sure you want to delete the model configuration "{configToDelete?.name}"? This action cannot be
            undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleConfirmDelete} color="error" variant="contained" disabled={deleteMutation.isLoading}>
            {deleteMutation.isLoading ? 'Deleting...' : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Test Dialog */}
      <Dialog open={testDialogOpen} onClose={() => setTestDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Test Model Configuration</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Test "{selectedConfig?.name}" with a sample message
          </Typography>
          <TextField
            fullWidth
            label="Test Message"
            value={testMessage}
            onChange={(e) => setTestMessage(e.target.value)}
            multiline
            rows={3}
            sx={{ mt: 1 }}
          />
          {selectedConfig && testResults[selectedConfig.id] && (
            <Box sx={{ mt: 2 }}>
              <Divider sx={{ mb: 2 }} />
              <Typography variant="subtitle2" gutterBottom>
                Test Result:
              </Typography>
              {testResults[selectedConfig.id].success ? (
                <Box>
                  <Alert severity="success" sx={{ mb: 2 }}>
                    Test completed successfully
                  </Alert>
                  <Typography variant="body2" sx={{ mb: 1 }}>
                    <strong>Response:</strong> {testResults[selectedConfig.id].response}
                  </Typography>
                  <Typography variant="body2" sx={{ mb: 1 }}>
                    <strong>Model:</strong> {testResults[selectedConfig.id].model_used}
                  </Typography>
                  <Typography variant="body2" sx={{ mb: 1 }}>
                    <strong>Prompt Applied:</strong> {testResults[selectedConfig.id].prompt_applied ? 'Yes' : 'No'}
                  </Typography>
                  <Typography variant="body2" sx={{ mb: 1 }}>
                    <strong>Knowledge Bases:</strong>{' '}
                    {testResults[selectedConfig.id].knowledge_bases_used?.join(', ') || 'None'}
                  </Typography>
                </Box>
              ) : (
                <Alert severity="error">
                  <Typography variant="subtitle2" gutterBottom>
                    Test failed
                  </Typography>
                  {testResults[selectedConfig.id].error}
                </Alert>
              )}
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setTestDialogOpen(false)}>Close</Button>
          <Button onClick={handleRunTest} variant="contained" disabled={!testMessage.trim() || testMutation.isLoading}>
            {testMutation.isLoading ? 'Testing...' : 'Run Test'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ModelConfigurations;
