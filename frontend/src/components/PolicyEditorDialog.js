import React, { useState, useEffect, useMemo } from 'react';
import { useQuery } from 'react-query';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Alert,
  Box,
  Paper,
  Typography,
  IconButton,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Switch,
  FormControlLabel,
  Autocomplete,
  Chip,
  Stack,
  CircularProgress,
} from '@mui/material';
import { Delete as DeleteIcon, Add as AddIcon } from '@mui/icons-material';
import {
  authAPI,
  groupsAPI,
  policyAPI,
  experiencesAPI,
  knowledgeBaseAPI,
  extractDataFromResponse,
  extractItemsFromResponse,
} from '../services/api';
import { pluginsAPI } from '../services/pluginsApi';
import { resolveUserId } from '../utils/userHelpers';

const EMPTY_FORM = {
  name: '',
  description: '',
  effect: 'allow',
  is_active: true,
  bindings: [],
  statements: [],
};

const toArray = (val) => {
  if (Array.isArray(val)) {
    return val;
  }
  if (val !== null && val !== '') {
    return [val];
  }
  return [];
};

const policyToFormData = (policy) => ({
  name: policy.name || '',
  description: policy.description || '',
  effect: policy.effect || 'allow',
  is_active: policy.is_active !== undefined ? policy.is_active : true,
  bindings: toArray(policy.bindings)
    .filter((b) => b && typeof b === 'object' && !Array.isArray(b))
    .map((b) => ({ actor_type: b.actor_type || 'user', actor_id: b.actor_id || '' })),
  statements: toArray(policy.statements)
    .filter((s) => s && typeof s === 'object' && !Array.isArray(s))
    .map((s) => ({
      actions: toArray(s.actions).map(String),
      resources: toArray(s.resources).map(String),
    })),
});

const BindingRow = ({ binding, index, onUpdate, onRemove, userOptions, groupOptions }) => (
  <Paper variant="outlined" sx={{ p: 2, display: 'flex', gap: 2, alignItems: 'center' }}>
    <FormControl size="small" sx={{ minWidth: 120 }}>
      <InputLabel>Type</InputLabel>
      <Select
        value={binding.actor_type}
        label="Type"
        onChange={(e) => onUpdate(index, { actor_type: e.target.value, actor_id: '' })}
      >
        <MenuItem value="user">User</MenuItem>
        <MenuItem value="group">Group</MenuItem>
      </Select>
    </FormControl>
    <Autocomplete
      size="small"
      sx={{ flex: 1 }}
      options={binding.actor_type === 'user' ? userOptions : groupOptions}
      getOptionLabel={(opt) => (typeof opt === 'string' ? opt : opt.label)}
      isOptionEqualToValue={(opt, val) => opt.value === (typeof val === 'string' ? val : val.value)}
      value={
        (binding.actor_type === 'user' ? userOptions : groupOptions).find((o) => o.value === binding.actor_id) ||
        (binding.actor_id ? { label: binding.actor_id, value: binding.actor_id } : null)
      }
      onChange={(_, newVal) =>
        onUpdate(index, { actor_id: (typeof newVal === 'string' ? newVal : newVal?.value) || '' })
      }
      freeSolo
      renderInput={(params) => (
        <TextField
          {...params}
          label={binding.actor_type === 'user' ? 'User' : 'Group'}
          placeholder={`Select or enter ${binding.actor_type} ID`}
        />
      )}
    />
    <IconButton size="small" color="error" aria-label={`Remove binding ${index + 1}`} onClick={() => onRemove(index)}>
      <DeleteIcon fontSize="small" />
    </IconButton>
  </Paper>
);

const StatementRow = ({ statement, index, onUpdate, onRemove, actionOptions, resourceOptions }) => (
  <Paper variant="outlined" sx={{ p: 2 }}>
    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1.5 }}>
      <Typography variant="body2" fontWeight="medium">
        Statement {index + 1}
      </Typography>
      <IconButton
        size="small"
        color="error"
        aria-label={`Remove statement ${index + 1}`}
        onClick={() => onRemove(index)}
      >
        <DeleteIcon fontSize="small" />
      </IconButton>
    </Box>
    <Stack spacing={2}>
      <Autocomplete
        multiple
        freeSolo
        size="small"
        options={actionOptions.filter((o) => !statement.actions.includes(o))}
        value={statement.actions}
        onChange={(_, newVal) => onUpdate(index, { actions: newVal })}
        renderTags={(value, getTagProps) =>
          value.map((action, i) => <Chip key={i} label={action} size="small" {...getTagProps({ index: i })} />)
        }
        renderInput={(params) => <TextField {...params} label="Actions" placeholder="Select or type a custom action" />}
      />
      <Autocomplete
        multiple
        freeSolo
        size="small"
        options={resourceOptions.filter((o) => !statement.resources.includes(o))}
        value={statement.resources}
        onChange={(_, newVal) => onUpdate(index, { resources: newVal })}
        renderTags={(value, getTagProps) =>
          value.map((resource, i) => <Chip key={i} label={resource} size="small" {...getTagProps({ index: i })} />)
        }
        renderInput={(params) => (
          <TextField {...params} label="Resources" placeholder="Select or type a custom resource" />
        )}
      />
    </Stack>
  </Paper>
);

const PolicyEditorDialog = ({ open, onClose, policy, onSave, isSaving, saveError }) => {
  const [formData, setFormData] = useState(EMPTY_FORM);
  const [viewMode, setViewMode] = useState('form');
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState(null);

  const { data: usersResponse } = useQuery('users', authAPI.getUsers, { enabled: open });
  const { data: groupsResponse } = useQuery('groups', groupsAPI.list, { enabled: open });
  const { data: actionsResponse } = useQuery('policyActions', policyAPI.actions, { enabled: open });
  const { data: experiencesResponse } = useQuery('experiences', () => experiencesAPI.list(), { enabled: open });
  const { data: pluginsResponse } = useQuery(['plugins', 'list'], pluginsAPI.list, { enabled: open });
  const { data: kbsResponse } = useQuery(['kbs', 'list'], () => knowledgeBaseAPI.list(), { enabled: open });

  const userOptions = useMemo(() => {
    const users = extractDataFromResponse(usersResponse) || [];
    return users.map((u) => ({
      label: `${u.name} (${u.email})`,
      value: resolveUserId(u),
    }));
  }, [usersResponse]);

  const groupOptions = useMemo(() => {
    const groups = extractItemsFromResponse(groupsResponse) || [];
    return groups.map((g) => ({ label: g.name, value: g.id }));
  }, [groupsResponse]);

  const actionOptions = useMemo(() => {
    const data = extractDataFromResponse(actionsResponse);
    return (data?.actions || []).map((a) => a.value);
  }, [actionsResponse]);

  const resourceOptions = useMemo(() => {
    const experiences = extractItemsFromResponse(experiencesResponse) || [];
    const plugins = extractDataFromResponse(pluginsResponse) || [];
    const kbs = extractItemsFromResponse(kbsResponse) || [];
    return [
      ...experiences.filter((e) => e.slug).map((e) => `experience:${e.slug}`),
      ...plugins.filter((p) => p.name).map((p) => `plugin:${p.name}`),
      ...kbs.filter((kb) => kb.slug).map((kb) => `kb:${kb.slug}`),
    ];
  }, [experiencesResponse, pluginsResponse, kbsResponse]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const data = policy ? policyToFormData(policy) : policyToFormData(EMPTY_FORM);
    setFormData(data);
    setJsonText(JSON.stringify(data, null, 2));
    setJsonError(null);
    setViewMode('form');
  }, [open, policy]);

  const updateField = (field, value) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  const addBinding = () => {
    setFormData((prev) => ({
      ...prev,
      bindings: [...prev.bindings, { actor_type: 'user', actor_id: '' }],
    }));
  };

  const updateBinding = (index, updates) => {
    setFormData((prev) => {
      const bindings = [...prev.bindings];
      bindings[index] = { ...bindings[index], ...updates };
      return { ...prev, bindings };
    });
  };

  const removeBinding = (index) => {
    setFormData((prev) => ({
      ...prev,
      bindings: prev.bindings.filter((_, i) => i !== index),
    }));
  };

  const addStatement = () => {
    setFormData((prev) => ({
      ...prev,
      statements: [...prev.statements, { actions: [], resources: [] }],
    }));
  };

  const updateStatement = (index, updates) => {
    setFormData((prev) => {
      const statements = [...prev.statements];
      statements[index] = { ...statements[index], ...updates };
      return { ...prev, statements };
    });
  };

  const removeStatement = (index) => {
    setFormData((prev) => ({
      ...prev,
      statements: prev.statements.filter((_, i) => i !== index),
    }));
  };

  const switchToJson = () => {
    setJsonText(JSON.stringify(formData, null, 2));
    setJsonError(null);
    setViewMode('json');
  };

  const switchToForm = () => {
    try {
      const parsed = JSON.parse(jsonText);
      setFormData(policyToFormData(parsed));
      setJsonError(null);
      setViewMode('form');
    } catch {
      setJsonError('Invalid JSON — fix errors before switching to form view.');
    }
  };

  const handleSave = () => {
    if (viewMode === 'json') {
      try {
        const parsed = JSON.parse(jsonText);
        onSave(policyToFormData(parsed));
      } catch {
        setJsonError('Invalid JSON. Please check your syntax and try again.');
      }
    } else {
      onSave(formData);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {policy ? 'Edit Policy' : 'Create Policy'}
        <Button size="small" variant="text" onClick={viewMode === 'form' ? switchToJson : switchToForm}>
          {viewMode === 'form' ? 'JSON Editor' : 'Form Editor'}
        </Button>
      </DialogTitle>
      <DialogContent dividers>
        <Alert severity="info" sx={{ mb: 2 }}>
          Policy changes can take up to 5 minutes to propagate.
        </Alert>
        {saveError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {saveError}
          </Alert>
        )}
        {jsonError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {jsonError}
          </Alert>
        )}

        {viewMode === 'json' ? (
          <TextField
            autoFocus
            fullWidth
            multiline
            rows={20}
            variant="outlined"
            value={jsonText}
            onChange={(e) => {
              setJsonText(e.target.value);
              setJsonError(null);
            }}
            inputProps={{ 'aria-label': 'Policy JSON editor' }}
            InputProps={{ sx: { fontFamily: 'monospace', fontSize: '0.875rem' } }}
            sx={{ mt: 1 }}
          />
        ) : (
          <Stack spacing={3} sx={{ mt: 1 }}>
            {/* Basic Fields */}
            <Stack spacing={2}>
              <TextField
                fullWidth
                label="Name"
                value={formData.name}
                onChange={(e) => updateField('name', e.target.value)}
                required
                size="small"
              />
              <TextField
                fullWidth
                label="Description"
                value={formData.description}
                onChange={(e) => updateField('description', e.target.value)}
                multiline
                rows={2}
                size="small"
              />
              <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
                <FormControl size="small" sx={{ minWidth: 140 }}>
                  <InputLabel>Effect</InputLabel>
                  <Select
                    value={formData.effect}
                    label="Effect"
                    onChange={(e) => updateField('effect', e.target.value)}
                  >
                    <MenuItem value="allow">Allow</MenuItem>
                    <MenuItem value="deny">Deny</MenuItem>
                  </Select>
                </FormControl>
                <FormControlLabel
                  control={
                    <Switch checked={formData.is_active} onChange={(e) => updateField('is_active', e.target.checked)} />
                  }
                  label="Active"
                />
              </Box>
            </Stack>

            {/* Bindings */}
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                Bindings
              </Typography>
              <Stack spacing={1}>
                {formData.bindings.map((binding, i) => (
                  <BindingRow
                    key={i}
                    binding={binding}
                    index={i}
                    onUpdate={updateBinding}
                    onRemove={removeBinding}
                    userOptions={userOptions}
                    groupOptions={groupOptions}
                  />
                ))}
              </Stack>
              <Button size="small" startIcon={<AddIcon />} onClick={addBinding} sx={{ mt: 1 }}>
                Add Binding
              </Button>
            </Box>

            {/* Statements */}
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                Statements
              </Typography>
              <Stack spacing={1.5}>
                {formData.statements.map((statement, i) => (
                  <StatementRow
                    key={i}
                    statement={statement}
                    index={i}
                    onUpdate={updateStatement}
                    onRemove={removeStatement}
                    actionOptions={actionOptions}
                    resourceOptions={resourceOptions}
                  />
                ))}
              </Stack>
              <Button size="small" startIcon={<AddIcon />} onClick={addStatement} sx={{ mt: 1 }}>
                Add Statement
              </Button>
            </Box>
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={handleSave} variant="contained" disabled={isSaving}>
          {isSaving ? <CircularProgress size={20} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default PolicyEditorDialog;
