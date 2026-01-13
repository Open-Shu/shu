import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
    Alert,
    Box,
    Button,
    Chip,
    CircularProgress,
    Divider,
    FormControl,
    Grid,
    InputLabel,
    MenuItem,
    Paper,
    Select,
    Stack,
    TextField,
    Typography,
    Tabs,
    Tab,
    Switch,
    FormControlLabel,
} from '@mui/material';
import {
    ArrowBack as BackIcon,
    Save as SaveIcon,
    PlayArrow as RunIcon,
} from '@mui/icons-material';
import {
    experiencesAPI,
    extractDataFromResponse,
    formatError,
} from '../services/api';
import { promptAPI } from '../api/prompts';
import ExperienceStepBuilder from './ExperienceStepBuilder';
import ExperienceRunDialog from './ExperienceRunDialog';
import ExperienceRunsList from './ExperienceRunsList';
import ExportExperienceButton from './ExportExperienceButton';
import TemplateVariableHints from './TemplateVariableHints';
import TriggerConfiguration from './shared/TriggerConfiguration';
import ModelConfigurationSelector from './shared/ModelConfigurationSelector';

export default function ExperienceEditor() {
    const { experienceId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const isNew = !experienceId;

    // Form state
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [visibility, setVisibility] = useState('draft');
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
    const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

    // Fetch existing experience for edit mode
    const experienceQuery = useQuery(
        ['experiences', 'detail', experienceId],
        () => experiencesAPI.get(experienceId).then(extractDataFromResponse),
        {
            enabled: !isNew && !!experienceId,
            staleTime: 0,
        }
    );

    // Fetch prompts for selector
    const promptsQuery = useQuery(
        ['prompts', 'list'],
        async () => {
            const result = await promptAPI.list();
            return result?.data?.items || result?.items || [];
        },
        { staleTime: 30000 }
    );

    const prompts = useMemo(() => {
        const items = promptsQuery.data || [];
        return Array.isArray(items) ? items : [];
    }, [promptsQuery.data]);

    // Initialize form from existing experience
    useEffect(() => {
        if (experienceQuery.data) {
            const exp = experienceQuery.data;
            setName(exp.name || '');
            setDescription(exp.description || '');
            setVisibility(exp.visibility || 'draft');
            setTriggerType(exp.trigger_type || 'manual');
            setTriggerConfig(exp.trigger_config || {});
            // Use model configuration only (no legacy fields)
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
    const createMutation = useMutation(
        (data) => experiencesAPI.create(data).then(extractDataFromResponse),
        {
            onSuccess: (result) => {
                queryClient.invalidateQueries(['experiences', 'list']);
                setSnackbar({ open: true, message: 'Experience created successfully!', severity: 'success' });
                navigate(`/admin/experiences/${result.id}/edit`);
            },
        }
    );

    // Update mutation
    const updateMutation = useMutation(
        (data) => experiencesAPI.update(experienceId, data).then(extractDataFromResponse),
        {
            onSuccess: () => {
                queryClient.invalidateQueries(['experiences', 'list']);
                queryClient.invalidateQueries(['experiences', 'detail', experienceId]);
                setIsDirty(false);
                setSnackbar({ open: true, message: 'Changes saved successfully!', severity: 'success' });
            },
        }
    );

    const handleFieldChange = (setter, fieldName) => (e) => {
        setter(e.target.value);
        setIsDirty(true);
        // Clear specific field error when it changes
        if (validationErrors[fieldName]) {
            const newErrors = { ...validationErrors };
            delete newErrors[fieldName];
            setValidationErrors(newErrors);
        }
    };

    const handleStepsChange = (newSteps) => {
        setSteps(newSteps);
        setIsDirty(true);
    };

    // Insert template variable at cursor position in inline prompt
    const handleInsertVariable = (variableText) => {
        const textarea = inlinePromptRef.current;
        if (!textarea) {
            // Fallback: append to end
            setInlinePromptTemplate(prev => prev + variableText);
            setIsDirty(true);
            return;
        }

        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const before = inlinePromptTemplate.substring(0, start);
        const after = inlinePromptTemplate.substring(end);

        setInlinePromptTemplate(before + variableText + after);
        setIsDirty(true);

        // Restore cursor position after the inserted text
        setTimeout(() => {
            textarea.focus();
            textarea.setSelectionRange(start + variableText.length, start + variableText.length);
        }, 0);
    };

    const handleSave = () => {
        // Validation
        const errors = {};
        if (!name.trim()) {
            errors.name = 'Name is required';
        }

        if (triggerType === 'scheduled' && !triggerConfig.scheduled_at) {
            errors.scheduled_at = 'Scheduled date/time is required';
        }

        if (triggerType === 'cron' && !triggerConfig.cron) {
            errors.cron = 'Cron expression is required';
        }

        // Validate model configuration - only validate if user has selected something
        // Empty string is valid (means no LLM synthesis)
        if (modelConfigurationId && modelConfigurationId.trim() === '') {
            // This shouldn't happen with the selector, but just in case
            errors.model_configuration_id = 'Invalid model configuration selection';
        }

        if (Object.keys(errors).length > 0) {
            setValidationErrors(errors);
            return;
        }

        // Validate and coerce maxRunSeconds to prevent NaN
        const parsedMaxRunSeconds = parseInt(maxRunSeconds, 10);
        const safeMaxRunSeconds = Number.isFinite(parsedMaxRunSeconds) ? parsedMaxRunSeconds : null;

        const payload = {
            name,
            description: description || null,
            visibility,
            trigger_type: triggerType,
            trigger_config: triggerConfig,
            // Use model configuration (no legacy fields)
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
                    <Button
                        variant="outlined"
                        startIcon={<BackIcon />}
                        onClick={handleBack}
                    >
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
                        <ExportExperienceButton
                            experienceId={experienceId}
                            experienceName={name}
                            variant="button"
                            size="medium"
                        />
                    )}
                    <Button
                        variant="outlined"
                        startIcon={<RunIcon />}
                        onClick={() => setRunDialogOpen(true)}
                        disabled={isNew || isDirty || isSaving}
                        title={
                            isNew ? "Save the experience first" :
                            isDirty ? "Save changes first" :
                            isSaving ? "Saving in progress" :
                            "Run this experience now"
                        }
                    >
                        Run Now
                    </Button>
                    <Button
                        variant="contained"
                        startIcon={<SaveIcon />}
                        onClick={handleSave}
                        disabled={!name.trim() || isSaving}
                    >
                        {isSaving ? 'Saving...' : 'Save'}
                    </Button>
                </Stack>
            </Stack>

            {/* Error display */}
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
                    {/* Left Column - Basic Info & LLM Config */}
                    <Grid item xs={12} md={12} xl={5}>
                        <Stack spacing={3}>
                            {/* Basic Info */}
                            <Paper sx={{ p: 3 }}>
                                <Typography variant="h6" gutterBottom>
                                    Basic Information
                                </Typography>
                                <Stack spacing={2}>
                                    <TextField
                                        label="Name"
                                        value={name}
                                        onChange={handleFieldChange(setName, 'name')}
                                        fullWidth
                                        required
                                        error={!!validationErrors.name}
                                        helperText={validationErrors.name}
                                    />
                                    <TextField
                                        label="Description"
                                        value={description}
                                        onChange={handleFieldChange(setDescription, 'description')}
                                        fullWidth
                                        multiline
                                        rows={3}
                                    />
                                    <Stack direction="row" spacing={2}>
                                        <FormControl fullWidth>
                                            <InputLabel>Visibility</InputLabel>
                                            <Select
                                                value={visibility}
                                                label="Visibility"
                                                onChange={handleFieldChange(setVisibility, 'visibility')}
                                            >
                                                <MenuItem value="draft">Draft</MenuItem>
                                                <MenuItem value="admin_only">Admin Only</MenuItem>
                                                <MenuItem value="published">Published</MenuItem>
                                            </Select>
                                        </FormControl>
                                        <TextField
                                            label="Max Run Time (s)"
                                            type="number"
                                            value={maxRunSeconds}
                                            onChange={handleFieldChange(setMaxRunSeconds, 'max_run_seconds')}
                                            fullWidth
                                            inputProps={{ min: 10, max: 600 }}
                                        />
                                    </Stack>
                                    <FormControlLabel
                                        control={
                                            <Switch
                                                checked={includePreviousRun}
                                                onChange={(e) => {
                                                    setIncludePreviousRun(e.target.checked);
                                                    setIsDirty(true);
                                                }}
                                            />
                                        }
                                        label="Include output from previous successful run in context"
                                    />
                                </Stack>
                            </Paper>

                            {/* Trigger Configuration */}
                            <Paper sx={{ p: 3 }}>
                                <Typography variant="h6" gutterBottom>
                                    Trigger Configuration
                                </Typography>
                                <TriggerConfiguration
                                    triggerType={triggerType}
                                    triggerConfig={triggerConfig}
                                    onTriggerTypeChange={(newType) => {
                                        setTriggerType(newType);
                                        setIsDirty(true);
                                    }}
                                    onTriggerConfigChange={(newConfig) => {
                                        setTriggerConfig(newConfig);
                                        setIsDirty(true);
                                        // Clear validation errors for trigger config fields when they change
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

                            {/* LLM Configuration */}
                            <Paper sx={{ p: 3 }}>
                                <Typography variant="h6" gutterBottom>
                                    LLM Configuration (Optional)
                                </Typography>
                                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                                    Configure the LLM to process step outputs and generate final results. 
                                    Leave empty if you only want to collect data without AI synthesis.
                                </Typography>
                                <Stack spacing={2}>
                                    <ModelConfigurationSelector
                                        modelConfigurationId={modelConfigurationId}
                                        onModelConfigurationChange={(newConfigId) => {
                                            setModelConfigurationId(newConfigId);
                                            setIsDirty(true);
                                            // Clear model configuration validation errors
                                            if (validationErrors.model_configuration_id) {
                                                const newErrors = { ...validationErrors };
                                                delete newErrors.model_configuration_id;
                                                setValidationErrors(newErrors);
                                            }
                                        }}
                                        validationErrors={validationErrors}
                                        required={false}
                                        showHelperText={true}
                                        label="Model Configuration"
                                        showDetails={true}
                                    />
                                    
                                    {/* Only show prompt options if model configuration is selected */}
                                    {modelConfigurationId && (
                                        <>
                                            <Divider />
                                            <FormControl fullWidth>
                                                <InputLabel>Prompt Template</InputLabel>
                                                <Select
                                                    value={promptId}
                                                    label="Prompt Template"
                                                    onChange={handleFieldChange(setPromptId, 'prompt_id')}
                                                >
                                                    <MenuItem value="">
                                                        <em>Use model configuration prompt</em>
                                                    </MenuItem>
                                                    {prompts.map((p) => (
                                                        <MenuItem key={p.id} value={p.id}>
                                                            {p.name}
                                                        </MenuItem>
                                                    ))}
                                                </Select>
                                            </FormControl>
                                            {!promptId && (
                                                <>
                                                    <TextField
                                                        label="Inline Prompt Template"
                                                        value={inlinePromptTemplate}
                                                        onChange={handleFieldChange(setInlinePromptTemplate, 'inline_prompt_template')}
                                                        fullWidth
                                                        multiline
                                                        rows={20}
                                                        placeholder="Use {{ step_outputs.step_key }} to reference step results"
                                                        helperText="Jinja2 template with access to step_outputs, user, and previous_run. Overrides model configuration prompt."
                                                        inputRef={inlinePromptRef}
                                                    />
                                                    <TemplateVariableHints
                                                        steps={steps}
                                                        includePreviousRun={includePreviousRun}
                                                        onInsert={handleInsertVariable}
                                                    />
                                                </>
                                            )}
                                        </>
                                    )}
                                    
                                    {/* Show helpful message when no model configuration is selected */}
                                    {!modelConfigurationId && (
                                        <Alert severity="info" sx={{ mt: 1 }}>
                                            No LLM synthesis configured. This experience will only collect and return step outputs without AI processing.
                                        </Alert>
                                    )}
                                </Stack>
                            </Paper>
                        </Stack>
                    </Grid>

                    {/* Right Column - Steps Builder */}
                    <Grid item xs={12} md={12} xl={7}>
                        <Paper sx={{ p: 3 }}>
                            <Typography variant="h6" gutterBottom>
                                Experience Steps
                            </Typography>
                            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                                Define the steps that gather data for this experience. Steps execute
                                in order and their outputs are available to subsequent steps and the
                                final prompt.
                            </Typography>
                            <ExperienceStepBuilder
                                steps={steps}
                                onChange={handleStepsChange}
                            />
                        </Paper>
                    </Grid>
                </Grid>
            )}

            {activeTab === 1 && (
                <ExperienceRunsList experienceId={experienceId} />
            )}

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
