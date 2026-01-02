import React, { useState, useMemo } from 'react';
import { useQuery } from 'react-query';
import {
    Alert,
    Box,
    Button,
    Card,
    CardContent,
    Collapse,
    FormControl,
    IconButton,
    InputLabel,
    MenuItem,
    Select,
    Stack,
    TextField,
    Tooltip,
    Typography,
} from '@mui/material';
import {
    Add as AddIcon,
    Delete as DeleteIcon,
    DragIndicator as DragIcon,
    ExpandMore as ExpandIcon,
    ExpandLess as CollapseIcon,
    Extension as PluginIcon,
    Storage as KBIcon,
    KeyboardArrowUp as MoveUpIcon,
    KeyboardArrowDown as MoveDownIcon,
} from '@mui/icons-material';
import { pluginsAPI } from '../services/pluginsApi';
import { knowledgeBaseAPI, extractDataFromResponse, extractItemsFromResponse } from '../services/api';
import SchemaForm, { buildDefaultValues } from './SchemaForm';

const StepTypeChip = ({ type }) => {
    const isPlugin = type === 'plugin';
    return (
        <Box
            sx={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 0.5,
                px: 1,
                py: 0.25,
                borderRadius: 1,
                bgcolor: isPlugin ? 'primary.50' : 'secondary.50',
                color: isPlugin ? 'primary.main' : 'secondary.main',
                fontSize: '0.75rem',
                fontWeight: 500,
            }}
        >
            {isPlugin ? <PluginIcon sx={{ fontSize: 14 }} /> : <KBIcon sx={{ fontSize: 14 }} />}
            {isPlugin ? 'Plugin' : 'Knowledge Base'}
        </Box>
    );
};

const StepCard = ({
    step,
    index,
    isFirst,
    isLast,
    onUpdate,
    onRemove,
    onMoveUp,
    onMoveDown,
    plugins,
    knowledgeBases,
}) => {
    const [expanded, setExpanded] = useState(true);
    const isPlugin = step.step_type === 'plugin';

    // Get available operations for selected plugin
    const selectedPlugin = useMemo(() => {
        if (!isPlugin) return null;
        return plugins.find(p => p.name === step.plugin_name);
    }, [isPlugin, plugins, step.plugin_name]);

    const availableOps = useMemo(() => {
        if (!selectedPlugin) return [];
        // Get operations from input_schema.properties.op.enum
        const enumOps = selectedPlugin?.input_schema?.properties?.op?.enum;
        if (Array.isArray(enumOps) && enumOps.length > 0) return enumOps;
        // Fallback to allowed_feed_ops if no enum
        if (Array.isArray(selectedPlugin?.allowed_feed_ops)) return selectedPlugin.allowed_feed_ops;
        return [];
    }, [selectedPlugin]);

    const handleFieldChange = (field) => (e) => {
        const value = e.target.value;
        const updates = { [field]: value };

        // Reset dependent fields when parent changes
        if (field === 'step_type') {
            updates.plugin_name = null;
            updates.plugin_op = null;
            updates.knowledge_base_id = null;
            updates.kb_query_template = null;
        }
        if (field === 'plugin_name') {
            updates.plugin_op = null;
        }

        onUpdate(updates);
    };

    const stepLabel = step.step_key || `Step ${index + 1}`;

    return (
        <Card
            variant="outlined"
            sx={{
                border: '1px solid',
                borderColor: 'divider',
                '&:hover': { borderColor: 'primary.light' },
            }}
        >
            <CardContent sx={{ py: 1, px: 2, '&:last-child': { pb: 1 } }}>
                {/* Header Row */}
                <Stack direction="row" alignItems="center" spacing={1}>
                    <IconButton size="small" sx={{ cursor: 'grab' }} disabled>
                        <DragIcon fontSize="small" />
                    </IconButton>

                    <Typography
                        variant="subtitle2"
                        sx={{ flex: 1, fontWeight: 600 }}
                    >
                        {stepLabel}
                    </Typography>

                    <StepTypeChip type={step.step_type} />

                    <Tooltip title="Move up">
                        <span>
                            <IconButton
                                size="small"
                                onClick={onMoveUp}
                                disabled={isFirst}
                            >
                                <MoveUpIcon fontSize="small" />
                            </IconButton>
                        </span>
                    </Tooltip>

                    <Tooltip title="Move down">
                        <span>
                            <IconButton
                                size="small"
                                onClick={onMoveDown}
                                disabled={isLast}
                            >
                                <MoveDownIcon fontSize="small" />
                            </IconButton>
                        </span>
                    </Tooltip>

                    <IconButton
                        size="small"
                        onClick={() => setExpanded(!expanded)}
                    >
                        {expanded ? <CollapseIcon /> : <ExpandIcon />}
                    </IconButton>

                    <Tooltip title="Remove step">
                        <IconButton
                            size="small"
                            color="error"
                            onClick={onRemove}
                        >
                            <DeleteIcon fontSize="small" />
                        </IconButton>
                    </Tooltip>
                </Stack>

                {/* Expanded Content */}
                <Collapse in={expanded}>
                    <Box sx={{ mt: 2, pl: 4 }}>
                        <Stack spacing={2}>
                            {/* Step Key */}
                            <TextField
                                label="Step Key"
                                value={step.step_key || ''}
                                onChange={handleFieldChange('step_key')}
                                size="small"
                                fullWidth
                                helperText="Unique identifier used to reference this step's output"
                            />

                            {/* Step Type */}
                            <FormControl fullWidth size="small">
                                <InputLabel>Step Type</InputLabel>
                                <Select
                                    value={step.step_type || 'plugin'}
                                    label="Step Type"
                                    onChange={handleFieldChange('step_type')}
                                >
                                    <MenuItem value="plugin">Plugin Call</MenuItem>
                                    <MenuItem value="knowledge_base">Knowledge Base Query</MenuItem>
                                </Select>
                            </FormControl>

                            {/* Plugin Configuration */}
                            {isPlugin && (
                                <>
                                    <FormControl fullWidth size="small">
                                        <InputLabel>Plugin</InputLabel>
                                        <Select
                                            value={step.plugin_name || ''}
                                            label="Plugin"
                                            onChange={handleFieldChange('plugin_name')}
                                        >
                                            {plugins.map((p) => (
                                                <MenuItem key={p.name} value={p.name}>
                                                    {p.display_name || p.name}
                                                </MenuItem>
                                            ))}
                                        </Select>
                                    </FormControl>

                                    {step.plugin_name && (
                                        <FormControl fullWidth size="small">
                                            <InputLabel>Operation</InputLabel>
                                            <Select
                                                value={step.plugin_op || ''}
                                                label="Operation"
                                                onChange={handleFieldChange('plugin_op')}
                                            >
                                                {availableOps.map((op) => (
                                                    <MenuItem key={op} value={op}>
                                                        {op}
                                                    </MenuItem>
                                                ))}
                                            </Select>
                                        </FormControl>
                                    )}

                                    {/* Plugin Parameters Form */}
                                    {step.plugin_op && selectedPlugin?.input_schema && (
                                        <Box sx={{ mt: 1 }}>
                                            <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                                                Parameters (only set values you want to override)
                                            </Typography>
                                            <SchemaForm
                                                schema={selectedPlugin.input_schema}
                                                values={step.params_template || {}}
                                                onChangeField={(key, type, value) => {
                                                    const newParams = { ...(step.params_template || {}) };
                                                    // Only keep non-empty values - empty means use backend default
                                                    if (value === '' || value === null || value === undefined) {
                                                        delete newParams[key];
                                                    } else {
                                                        newParams[key] = value;
                                                    }
                                                    // If empty object, set to null
                                                    onUpdate({ params_template: Object.keys(newParams).length > 0 ? newParams : null });
                                                }}
                                                hideKeys={new Set(['op', 'kb_id'])}
                                            />
                                        </Box>
                                    )}
                                </>
                            )}

                            {/* KB Query Configuration */}
                            {!isPlugin && (
                                <>
                                    <FormControl fullWidth size="small">
                                        <InputLabel>Knowledge Base</InputLabel>
                                        <Select
                                            value={step.knowledge_base_id || ''}
                                            label="Knowledge Base"
                                            onChange={handleFieldChange('knowledge_base_id')}
                                        >
                                            {knowledgeBases.map((kb) => (
                                                <MenuItem key={kb.id} value={kb.id}>
                                                    {kb.name}
                                                </MenuItem>
                                            ))}
                                        </Select>
                                    </FormControl>

                                    <TextField
                                        label="Query Template"
                                        value={step.kb_query_template || ''}
                                        onChange={handleFieldChange('kb_query_template')}
                                        size="small"
                                        fullWidth
                                        multiline
                                        rows={2}
                                        placeholder="Search for {{ user.name }}'s documents"
                                        helperText="Jinja2 template for the KB query"
                                    />
                                </>
                            )}

                            {/* Condition Template - actually just a required step key */}
                            <TextField
                                label="Required Step (Optional)"
                                value={step.condition_template || ''}
                                onChange={handleFieldChange('condition_template')}
                                size="small"
                                fullWidth
                                placeholder="previous_step"
                                helperText="Step key that must succeed before this step runs"
                            />
                        </Stack>
                    </Box>
                </Collapse>
            </CardContent>
        </Card>
    );
};

export default function ExperienceStepBuilder({ steps, onChange }) {
    // Fetch plugins
    const pluginsQuery = useQuery(
        ['plugins', 'list'],
        () => pluginsAPI.list().then(extractDataFromResponse),
        { staleTime: 30000 }
    );

    // Fetch knowledge bases
    const kbQuery = useQuery(
        ['kbs', 'list'],
        () => knowledgeBaseAPI.list().then(extractItemsFromResponse),
        { staleTime: 30000 }
    );

    const plugins = useMemo(() => {
        const items = pluginsQuery.data || [];
        return Array.isArray(items) ? items : [];
    }, [pluginsQuery.data]);

    const knowledgeBases = useMemo(() => {
        const items = kbQuery.data || [];
        return Array.isArray(items) ? items : [];
    }, [kbQuery.data]);

    const handleAddStep = (type) => {
        const newStep = {
            step_key: `step_${steps.length + 1}`,
            step_type: type,
            order: steps.length,
            plugin_name: null,
            plugin_op: null,
            knowledge_base_id: null,
            kb_query_template: null,
            params_template: null,
            condition_template: null,
        };
        onChange([...steps, newStep]);
    };

    const handleUpdateStep = (index) => (updates) => {
        const newSteps = [...steps];
        newSteps[index] = { ...newSteps[index], ...updates };
        onChange(newSteps);
    };

    const handleRemoveStep = (index) => () => {
        const newSteps = steps.filter((_, i) => i !== index);
        onChange(newSteps);
    };

    const handleMoveUp = (index) => () => {
        if (index === 0) return;
        const newSteps = [...steps];
        [newSteps[index - 1], newSteps[index]] = [newSteps[index], newSteps[index - 1]];
        onChange(newSteps);
    };

    const handleMoveDown = (index) => () => {
        if (index >= steps.length - 1) return;
        const newSteps = [...steps];
        [newSteps[index], newSteps[index + 1]] = [newSteps[index + 1], newSteps[index]];
        onChange(newSteps);
    };

    return (
        <Box>
            {/* Steps List */}
            <Stack spacing={2} sx={{ mb: 2 }}>
                {steps.length === 0 ? (
                    <Alert severity="info">
                        No steps configured. Add steps to gather data for your experience.
                    </Alert>
                ) : (
                    steps.map((step, index) => (
                        <StepCard
                            key={index}
                            step={step}
                            index={index}
                            isFirst={index === 0}
                            isLast={index === steps.length - 1}
                            onUpdate={handleUpdateStep(index)}
                            onRemove={handleRemoveStep(index)}
                            onMoveUp={handleMoveUp(index)}
                            onMoveDown={handleMoveDown(index)}
                            plugins={plugins}
                            knowledgeBases={knowledgeBases}
                        />
                    ))
                )}
            </Stack>

            {/* Add Step Buttons */}
            <Stack direction="row" spacing={1}>
                <Button
                    variant="outlined"
                    size="small"
                    startIcon={<PluginIcon />}
                    onClick={() => handleAddStep('plugin')}
                >
                    Add Plugin Step
                </Button>
                <Button
                    variant="outlined"
                    size="small"
                    startIcon={<KBIcon />}
                    onClick={() => handleAddStep('knowledge_base')}
                >
                    Add KB Query Step
                </Button>
            </Stack>
        </Box>
    );
}
