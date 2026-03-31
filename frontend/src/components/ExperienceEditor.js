import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { Alert, Box, Button, Chip, CircularProgress, Grid, Paper, Stack, Typography, Tabs, Tab } from '@mui/material';
import { ArrowBack as BackIcon, Save as SaveIcon, PlayArrow as RunIcon } from '@mui/icons-material';
import { experiencesAPI, extractDataFromResponse, formatError } from '../services/api';
import ExperienceStepBuilder from './ExperienceStepBuilder';
import ExperienceRunDialog from './ExperienceRunDialog';
import ExperienceRunsList from './ExperienceRunsList';
import ExportExperienceButton from './ExportExperienceButton';
import TriggerConfiguration from './shared/TriggerConfiguration';
import ExperienceBasicInfoPanel from './shared/ExperienceBasicInfoPanel';
import ExperienceLLMPanel from './shared/ExperienceLLMPanel';

export default function ExperienceEditor() {
  const { experienceId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = !experienceId;

  // Form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState('draft');
  const [scope, setScope] = useState('user');
  const [triggerType, setTriggerType] = useState('manual');
  const [triggerConfig, setTriggerConfig] = useState({});
  const [modelConfigurationId, setModelConfigurationId] = useState('');
  const [promptId, setPromptId] = useState('');
  const [inlinePromptTemplate, setInlinePromptTemplate] = useState('');
  const [steps, setSteps] = useState([]);
  const [maxRunSeconds, setMaxRunSeconds] = useState(120);
  const [includePreviousRun, setIncludePreviousRun] = useState(false);
  const [runDialogOpen, setRunDialogOpen] = useState(false);

  const [validationErrors, setValidationErrors] = useState({});

  // Ref for inline prompt textarea to insert variables at cursor
  const inlinePromptRef = useRef(null);

  // Read tab from URL query parameter
  const [searchParams] = useSearchParams();
  const initialTab = parseInt(searchParams.get('tab') || '0', 10);
  const [activeTab, setActiveTab] = useState(initialTab);
  const [isDirty, setIsDirty] = useState(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success',
  });

  // Fetch existing experience for edit mode
  const experienceQuery = useQuery(
    ['experiences', 'detail', experienceId],
    () => experiencesAPI.get(experienceId).then(extractDataFromResponse),
    {
      enabled: !isNew && !!experienceId,
      staleTime: 0,
    }
  );

  // Initialize form from existing experience
  useEffect(() => {
    if (experienceQuery.data) {
      const exp = experienceQuery.data;
      setName(exp.name || '');
      setDescription(exp.description || '');
      setVisibility(exp.visibility || 'draft');
      setScope(exp.scope || 'user');
      setTriggerType(exp.trigger_type || 'manual');
      setTriggerConfig(exp.trigger_config || {});
      setModelConfigurationId(exp.model_configuration_id || '');
      setPromptId(exp.prompt_id || '');
      setInlinePromptTemplate(exp.inline_prompt_template || '');
      setSteps(exp.steps || []);
      setMaxRunSeconds(exp.max_run_seconds || 120);
      setIncludePreviousRun(exp.include_previous_run || false);
      setIsDirty(false);
    }
  }, [experienceQuery.data]);

  // Create mutation
  const createMutation = useMutation((data) => experiencesAPI.create(data).then(extractDataFromResponse), {
    onSuccess: (result) => {
      queryClient.invalidateQueries(['experiences', 'list']);
      setSnackbar({
        open: true,
        message: 'Experience created successfully!',
        severity: 'success',
      });
      navigate(`/admin/experiences/${result.id}/edit`);
    },
  });

  // Update mutation
  const updateMutation = useMutation(
    (data) => experiencesAPI.update(experienceId, data).then(extractDataFromResponse),
    {
      onSuccess: () => {
        queryClient.invalidateQueries(['experiences', 'list']);
        queryClient.invalidateQueries(['experiences', 'detail', experienceId]);
        setIsDirty(false);
        setSnackbar({
          open: true,
          message: 'Changes saved successfully!',
          severity: 'success',
        });
      },
    }
  );

  const markDirty = () => setIsDirty(true);

  const clearValidationError = (fieldName) => {
    if (validationErrors[fieldName]) {
      const newErrors = { ...validationErrors };
      delete newErrors[fieldName];
      setValidationErrors(newErrors);
    }
  };

  const handleFieldChange = (setter, fieldName) => (e) => {
    setter(e.target.value);
    markDirty();
    clearValidationError(fieldName);
  };

  // Wrapper for sub-panels: returns onChange handler keyed by field name
  const makePanelFieldChange = (fieldName) => {
    const setterMap = {
      name: setName,
      description: setDescription,
      visibility: setVisibility,
      scope: setScope,
      max_run_seconds: setMaxRunSeconds,
      prompt_id: setPromptId,
      inline_prompt_template: setInlinePromptTemplate,
    };
    const setter = setterMap[fieldName];
    if (typeof setter !== 'function') {
      console.warn(`makePanelFieldChange: unknown field "${fieldName}"`);
      return () => {};
    }
    return handleFieldChange(setter, fieldName);
  };

  const handleStepsChange = (newSteps) => {
    setSteps(newSteps);
    markDirty();
  };

  // Insert template variable at cursor position in inline prompt
  const handleInsertVariable = (variableText) => {
    const textarea = inlinePromptRef.current;
    if (!textarea) {
      setInlinePromptTemplate((prev) => prev + variableText);
      markDirty();
      return;
    }

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const before = inlinePromptTemplate.substring(0, start);
    const after = inlinePromptTemplate.substring(end);

    setInlinePromptTemplate(before + variableText + after);
    markDirty();

    setTimeout(() => {
      textarea.focus();
      textarea.setSelectionRange(start + variableText.length, start + variableText.length);
    }, 0);
  };

  const handleSave = () => {
    const errors = {};
    if (!name.trim()) {
      errors.name = 'Name is required';
    }

    if (triggerType === 'scheduled' && !triggerConfig.scheduled_at) {
      errors.scheduled_at = 'Scheduled date/time is required';
    }

    if (triggerType === 'cron') {
      if (!triggerConfig.cron) {
        errors.cron = 'Cron expression is required';
      }
      if (!triggerConfig.timezone) {
        errors.timezone = 'Timezone is required for recurring schedules';
      }
    }

    if (modelConfigurationId && modelConfigurationId.trim() === '') {
      errors.model_configuration_id = 'Invalid model configuration selection';
    }

    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      return;
    }

    const parsedMaxRunSeconds = parseInt(maxRunSeconds, 10);
    const safeMaxRunSeconds = Number.isFinite(parsedMaxRunSeconds) ? parsedMaxRunSeconds : null;

    const payload = {
      name,
      description: description || null,
      visibility,
      scope,
      trigger_type: triggerType,
      trigger_config: triggerConfig,
      model_configuration_id: modelConfigurationId || null,
      prompt_id: promptId || null,
      inline_prompt_template: inlinePromptTemplate || null,
      max_run_seconds: safeMaxRunSeconds,
      include_previous_run: includePreviousRun,
      steps: steps.map((step, index) => ({
        step_key: step.step_key || `step_${index}`,
        step_type: step.step_type,
        order: index,
        plugin_name: step.plugin_name || null,
        plugin_op: step.plugin_op || null,
        knowledge_base_id: step.knowledge_base_id || null,
        kb_query_template: step.kb_query_template || null,
        params_template: step.params_template || null,
        auth_override: step.auth_override || null,
        condition_template: step.condition_template || null,
      })),
    };

    if (isNew) {
      createMutation.mutate(payload);
    } else {
      updateMutation.mutate(payload);
    }
  };

  const handleBack = () => {
    if (isDirty) {
      if (!window.confirm('You have unsaved changes. Are you sure you want to leave?')) {
        return;
      }
    }
    navigate('/admin/experiences');
  };

  const isLoading = experienceQuery.isLoading;
  const isSaving = createMutation.isLoading || updateMutation.isLoading;
  const error = createMutation.error || updateMutation.error;

  if (isLoading) {
    return (
      <Box display="flex" alignItems="center" justifyContent="center" py={8}>
        <Stack alignItems="center" spacing={2}>
          <CircularProgress size={40} />
          <Typography variant="body2" color="text.secondary">
            Loading experience...
          </Typography>
        </Stack>
      </Box>
    );
  }

  if (experienceQuery.isError) {
    return (
      <Box p={3} display="flex" justifyContent="center">
        <Paper sx={{ p: 4, maxWidth: 600, textAlign: 'center' }}>
          <Typography variant="h6" color="error" gutterBottom>
            Error Loading Experience
          </Typography>
          <Typography variant="body1" color="text.secondary" paragraph>
            {formatError(experienceQuery.error || 'Unknown error')}
          </Typography>
          <Stack direction="row" spacing={2} justifyContent="center" mt={2}>
            <Button variant="outlined" onClick={handleBack}>
              Back to List
            </Button>
            <Button variant="contained" onClick={() => experienceQuery.refetch()}>
              Retry
            </Button>
          </Stack>
        </Paper>
      </Box>
    );
  }

  return (
    <Box p={3}>
      {/* Header */}
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={3}>
        <Stack direction="row" alignItems="center" spacing={2}>
          <Button variant="outlined" startIcon={<BackIcon />} onClick={handleBack}>
            Back
          </Button>
          <Typography variant="h4" sx={{ fontWeight: 600 }}>
            {isNew ? 'New Experience' : 'Edit Experience'}
          </Typography>
        </Stack>
        <Stack direction="row" alignItems="center" spacing={2}>
          {snackbar.open && (
            <Chip
              label={snackbar.message}
              color="success"
              size="small"
              onDelete={() => setSnackbar({ ...snackbar, open: false })}
            />
          )}
          {!isNew && (
            <ExportExperienceButton experienceId={experienceId} experienceName={name} variant="button" size="medium" />
          )}
          <Button
            variant="outlined"
            startIcon={<RunIcon />}
            onClick={() => setRunDialogOpen(true)}
            disabled={isNew || isDirty || isSaving}
            title={
              isNew
                ? 'Save the experience first'
                : isDirty
                  ? 'Save changes first'
                  : isSaving
                    ? 'Saving in progress'
                    : 'Run this experience now'
            }
          >
            Run Now
          </Button>
          <Button variant="contained" startIcon={<SaveIcon />} onClick={handleSave} disabled={!name.trim() || isSaving}>
            {isSaving ? 'Saving...' : 'Save'}
          </Button>
        </Stack>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {formatError(error)}
        </Alert>
      )}

      <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 3 }}>
        <Tabs value={activeTab} onChange={(e, v) => setActiveTab(v)}>
          <Tab label="Configuration" />
          <Tab label="Run History" disabled={isNew} />
        </Tabs>
      </Box>

      {activeTab === 0 && (
        <Grid container spacing={3}>
          <Grid item xs={12} md={12} xl={5}>
            <Stack spacing={3}>
              <Paper sx={{ p: 3 }}>
                <Typography variant="h6" gutterBottom>
                  Basic Information
                </Typography>
                <ExperienceBasicInfoPanel
                  name={name}
                  description={description}
                  visibility={visibility}
                  scope={scope}
                  maxRunSeconds={maxRunSeconds}
                  includePreviousRun={includePreviousRun}
                  onFieldChange={makePanelFieldChange}
                  onIncludePreviousRunChange={(e) => {
                    setIncludePreviousRun(e.target.checked);
                    markDirty();
                  }}
                  validationErrors={validationErrors}
                />
              </Paper>

              <Paper sx={{ p: 3 }}>
                <Typography variant="h6" gutterBottom>
                  Trigger Configuration
                </Typography>
                <TriggerConfiguration
                  triggerType={triggerType}
                  triggerConfig={triggerConfig}
                  onTriggerTypeChange={(newType) => {
                    setTriggerType(newType);
                    markDirty();
                  }}
                  onTriggerConfigChange={(newConfig) => {
                    setTriggerConfig(newConfig);
                    markDirty();
                    if (validationErrors.scheduled_at || validationErrors.cron || validationErrors.timezone) {
                      const newErrors = { ...validationErrors };
                      delete newErrors.scheduled_at;
                      delete newErrors.cron;
                      delete newErrors.timezone;
                      setValidationErrors(newErrors);
                    }
                  }}
                  validationErrors={validationErrors}
                  required={false}
                  showHelperText={true}
                />
              </Paper>

              <Paper sx={{ p: 3 }}>
                <Typography variant="h6" gutterBottom>
                  LLM Configuration (Optional)
                </Typography>
                <ExperienceLLMPanel
                  modelConfigurationId={modelConfigurationId}
                  promptId={promptId}
                  inlinePromptTemplate={inlinePromptTemplate}
                  steps={steps}
                  includePreviousRun={includePreviousRun}
                  onModelConfigurationChange={(newConfigId) => {
                    setModelConfigurationId(newConfigId);
                    markDirty();
                    clearValidationError('model_configuration_id');
                  }}
                  onFieldChange={makePanelFieldChange}
                  onInsertVariable={handleInsertVariable}
                  validationErrors={validationErrors}
                  inlinePromptRef={inlinePromptRef}
                />
              </Paper>
            </Stack>
          </Grid>

          <Grid item xs={12} md={12} xl={7}>
            <Paper sx={{ p: 3 }}>
              <Typography variant="h6" gutterBottom>
                Experience Steps
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Define the steps that gather data for this experience. Steps execute in order and their outputs are
                available to subsequent steps and the final prompt.
              </Typography>
              <ExperienceStepBuilder steps={steps} onChange={handleStepsChange} />
            </Paper>
          </Grid>
        </Grid>
      )}

      {activeTab === 1 && <ExperienceRunsList experienceId={experienceId} timezone={triggerConfig?.timezone} />}

      <ExperienceRunDialog
        open={runDialogOpen}
        onClose={() => setRunDialogOpen(false)}
        experienceId={experienceId}
        experienceName={name}
        steps={steps}
      />
    </Box>
  );
}
