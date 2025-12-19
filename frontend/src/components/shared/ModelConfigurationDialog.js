import React, { useEffect, useMemo, useRef } from 'react';
import { useQueryClient } from 'react-query';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Grid,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Button,
  Autocomplete,
  Paper,
  Typography,
  Box,
  Tooltip,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  FormControlLabel,
  Switch,
  Alert,
} from '@mui/material';
import { InfoOutlined, ExpandMore as ExpandMoreIcon } from '@mui/icons-material';
import NotImplemented from '../NotImplemented';

const ModelConfigurationDialog = ({
  open,
  onClose,
  title,
  formData,
  setFormData,
  providers,
  models,
  prompts,
  knowledgeBases,
  knowledgeBasesLoading,
  promptsLoading,
  visibleParams,
  paramOverrides,
  setParamOverrides,
  advancedJson,
  setAdvancedJson,
  advancedJsonError,
  setAdvancedJsonError,
  onSubmit,
  submitLabel,
  isSubmitting,
  submitError,
}) => {
  const queryClient = useQueryClient();
  const selectedKnowledgeBases = useMemo(() => {
    if (!knowledgeBases || !Array.isArray(knowledgeBases) || !formData.knowledge_base_ids) return [];
    return knowledgeBases.filter((kb) => formData.knowledge_base_ids.includes(kb.id));
  }, [knowledgeBases, formData.knowledge_base_ids]);

  const parseTextFieldValue = (key, spec) => {
    const val = paramOverrides[key];
    if (val === undefined || val === null) return "";
    // if user input is an object/array we stringify for display
    if ((spec.type === "object" || spec.type === "array") && typeof val !== "string") {
      try {
        return JSON.stringify(val);
      } catch {
        return String(val);
      }
    }
    // otherwise it’s already a string (user typed) or primitive
    return String(val);
  };

  // Invalidate side-call config query after successful save to refresh the chip
  const handleSaveSuccess = () => {
    queryClient.invalidateQueries('side-call-config');
  };

  // Keep typed parameters and Advanced JSON in sync
  const visibleParamKeys = useMemo(() => new Set(visibleParams.map(([k]) => k)), [visibleParams]);

  const deepClone = (val) => {
    try {
      return JSON.parse(JSON.stringify(val));
    } catch {
      return val;
    }
  };

  const deepEqual = (a, b) => {
    try {
      return JSON.stringify(a) === JSON.stringify(b);
    } catch {
      return a === b;
    }
  };

  const selectedProvider = useMemo(
    () => (providers || []).find((p) => p.id === formData.llm_provider_id),
    [providers, formData.llm_provider_id]
  );

  const providerCapabilities = useMemo(
    () => (selectedProvider?.provider_capabilities && typeof selectedProvider.provider_capabilities === 'object'
      ? selectedProvider.provider_capabilities
      : {}),
    [selectedProvider]
  );
  const lastProviderIdRef = useRef(null);

  const mapCapabilityKey = (capKey) => (capKey.startsWith('supports_') ? capKey : `supports_${capKey}`);

  const capabilityToggles = useMemo(() => {
    const funcs = formData.functionalities || {};
    return Object.entries(providerCapabilities)
      .map(([capKey, capVal]) => {
        const funcKey = mapCapabilityKey(capKey);
        const value = funcs[funcKey] !== undefined ? funcs[funcKey] : !!capVal?.value;
        const label = capVal?.label || `Supports ${capKey}`;
        return { funcKey, value, label };
      })
      .filter(Boolean);
  }, [providerCapabilities, formData.functionalities]);

  useEffect(() => {
    if (!selectedProvider) return;
    const providerId = selectedProvider.id;
    const providerChanged = providerId && providerId !== lastProviderIdRef.current;
    setFormData((prev) => {
      const prevFuncs = prev.functionalities || {};
      const prevHasFuncs = Object.keys(prevFuncs).length > 0;
      const nextFuncs = {};
      // When switching providers, only seed defaults if we don't already have persisted values.
      if (providerChanged && prevHasFuncs) {
        return prev;
      }
      Object.entries(providerCapabilities).forEach(([capKey, capVal]) => {
        const funcKey = mapCapabilityKey(capKey);
        const existing = prevFuncs[funcKey];
        const defaultVal = !!capVal?.value;
        nextFuncs[funcKey] = providerChanged ? defaultVal : (existing !== undefined ? existing : defaultVal);
      });
      if (deepEqual(prevFuncs, nextFuncs)) return prev;
      return { ...prev, functionalities: nextFuncs };
    });
    lastProviderIdRef.current = providerId || null;
  }, [selectedProvider, providerCapabilities, setFormData]);

  const matchOptionIndex = (arr, optVal) => {
    if (!Array.isArray(arr)) return -1;
    return arr.findIndex((v) => {
      if (v && optVal && typeof v === 'object' && typeof optVal === 'object' && v.type && optVal.type) {
        return v.type === optVal.type;
      }
      return deepEqual(v, optVal);
    });
  };

  const renderArrayWithItems = (paramKey, spec, currentVal, onChange) => {
    const itemsSpec = spec.items;
    const entries = Array.isArray(currentVal) ? currentVal : [];
    const addEntry = () => {
      let initial = {};
      if (itemsSpec?.type === 'string') initial = '';
      else if (itemsSpec?.type === 'number' || itemsSpec?.type === 'integer') initial = 0;
      onChange([...(entries || []), initial]);
    };
    const updateEntry = (idx, val) => {
      const next = [...entries];
      next[idx] = val;
      onChange(next);
    };
    const removeEntry = (idx) => {
      const next = [...entries];
      next.splice(idx, 1);
      onChange(next);
    };
    return (
      <Box sx={{ pl: 1 }}>
        {entries.map((entry, idx) => (
          <Paper key={`${paramKey}-item-${idx}`} variant="outlined" sx={{ p: 1, mb: 1 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
              <Typography variant="body2">Entry {idx + 1}</Typography>
              <Button size="small" onClick={() => removeEntry(idx)}>Remove</Button>
            </Box>
            {itemsSpec && itemsSpec.properties
              ? Object.entries(itemsSpec.properties).map(([propKey, propSpec]) =>
                renderSchemaField(
                  propKey,
                  propSpec,
                  entry ? entry[propKey] : undefined,
                  (val) => updateEntry(idx, setByPath(entry || {}, propKey, val))
                )
              )
              : (
                <TextField
                  fullWidth
                  size="small"
                  label={itemsSpec?.label || `${paramKey} item`}
                  value={(() => {
                    if (entry === undefined || entry === null) return '';
                    if (typeof entry === 'string' || typeof entry === 'number' || typeof entry === 'boolean') return entry;
                    try { return JSON.stringify(entry); } catch { return String(entry); }
                  })()}
                  onChange={(e) => {
                    let val = e.target.value;
                    if (itemsSpec?.type === 'object' || itemsSpec?.type === 'array') {
                      try { val = JSON.parse(val); } catch { /* keep as raw */ }
                    }
                    updateEntry(idx, val);
                  }}
                />
              )
            }
          </Paper>
        ))}
        <Button size="small" onClick={addEntry}>Add {spec.label || itemsSpec?.label || 'entry'}</Button>
      </Box>
    );
  };

  const setByPath = (obj, path, value) => {
    const parts = path.split('.');
    const clone = Array.isArray(obj) ? [...obj] : { ...(obj || {}) };
    let cur = clone;
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (i === parts.length - 1) {
        cur[p] = value;
      } else {
        cur[p] = typeof cur[p] === 'object' && cur[p] !== null ? { ...cur[p] } : {};
        cur = cur[p];
      }
    }
    return clone;
  };

  const updateParamValue = (key, nextVal) => {
    handleParamChange(key, nextVal);
  };

  const optionLabelForValue = (options, value) => {
    if (!Array.isArray(options)) return undefined;
    const found = options.find((opt) => {
      if (deepEqual(opt.value, value)) return true;
      if (opt.value && value && typeof opt.value === 'object' && typeof value === 'object') {
        if (opt.value.type && value.type && opt.value.type === value.type) return true;
      }
      return false;
    });
    return found?.label;
  };

  const optionByValue = (options, value) => {
    if (!Array.isArray(options)) return undefined;
    return options.find((opt) => {
      if (deepEqual(opt.value, value)) return true;
      if (opt.value && value && typeof opt.value === 'object' && typeof value === 'object') {
        if (opt.value.type && value.type && opt.value.type === value.type) return true;
      }
      return false;
    });
  };

  const renderSchemaField = (fieldKey, fieldSpec, currentVal, onChange) => {
    const commonProps = { fullWidth: true, size: 'small', label: fieldSpec.label || fieldKey };
    if (fieldSpec.type === 'enum' && Array.isArray(fieldSpec.options)) {
      return (
        <FormControl fullWidth size="small" sx={{ mb: 1 }} key={fieldKey}>
          <InputLabel>{fieldSpec.label || fieldKey}</InputLabel>
          <Select
            value={
              currentVal === undefined || currentVal === null
                ? ''
                : currentVal
            }
            label={fieldSpec.label || fieldKey}
            renderValue={(selected) => optionLabelForValue(fieldSpec.options, selected) || selected}
            onChange={(e) => {
              const valRaw = e.target.value;
              if (valRaw === '' || valRaw === null) {
                onChange(undefined);
                return;
              }
              onChange(valRaw);
            }}
          >
            {(fieldSpec.options || []).map((opt, idx) => (
              <MenuItem key={`${fieldKey}-opt-${idx}`} value={opt.value}>
                {opt.label || String(opt.value)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      );
    }
    if (fieldSpec.type === 'boolean') {
      return (
        <Box key={fieldKey} sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <Switch
            checked={!!currentVal}
            onChange={(e) => onChange(e.target.checked)}
          />
          <Typography variant="body2">{fieldSpec.label || fieldKey}</Typography>
        </Box>
      );
    }
    if (fieldSpec.type === 'array' && fieldSpec.items) {
      return (
        <Box key={fieldKey} sx={{ mb: 1 }}>
          <Typography variant="subtitle2">{fieldSpec.label || fieldKey}</Typography>
          {fieldSpec.description ? (
            <Typography variant="body2" color="text.secondary">{fieldSpec.description}</Typography>
          ) : null}
          {renderArrayWithItems(fieldKey, fieldSpec, currentVal, onChange)}
        </Box>
      );
    }
    if (fieldSpec.type === 'object' && fieldSpec.properties) {
      return (
        <Box key={fieldKey} sx={{ pl: 1, mb: 1 }}>
          {Object.entries(fieldSpec.properties).map(([propKey, propSpec]) =>
            renderSchemaField(
              propKey,
              propSpec,
              (currentVal || {})[propKey],
              (val) => {
                const base = (currentVal && typeof currentVal === 'object') ? { ...currentVal } : {};
                onChange(setByPath(base, propKey, val));
              }
            )
          )}
        </Box>
      );
    }
    const handlePrimitiveChange = (raw) => {
      if (raw === '') {
        onChange(undefined);
        return;
      }
      let nextVal = raw;
      if (fieldSpec.type === 'number') {
        const n = parseFloat(raw);
        nextVal = Number.isNaN(n) ? raw : n;
      } else if (fieldSpec.type === 'integer') {
        const n = parseInt(raw, 10);
        nextVal = Number.isNaN(n) ? raw : n;
      } else if (fieldSpec.type === 'object' || fieldSpec.type === 'array') {
        try {
          nextVal = JSON.parse(raw);
        } catch {
          nextVal = raw;
        }
      }
      onChange(nextVal);
    };
    return (
      <TextField
        key={fieldKey}
        {...commonProps}
        type={fieldSpec.type === 'number' || fieldSpec.type === 'integer' ? 'number' : 'text'}
        value={
          currentVal === undefined || currentVal === null
            ? ''
            : typeof currentVal === 'string' || typeof currentVal === 'number' || typeof currentVal === 'boolean'
              ? currentVal
              : (() => {
                try {
                  return JSON.stringify(currentVal);
                } catch {
                  return String(currentVal);
                }
              })()
        }
        onChange={(e) => handlePrimitiveChange(e.target.value)}
      />
    );
  };

  const safeParseJson = (text) => {
    try {
      return { ok: true, obj: text && text.trim() ? JSON.parse(text) : {} };
    } catch (e) {
      return { ok: false, error: e };
    }
  };

  const handleParamChange = (key, nextVal, isDelete = false) => {
    // Update typed overrides first
    setParamOverrides((prev) => {
      const next = { ...prev };
      if (isDelete) delete next[key]; else next[key] = nextVal;
      return next;
    });

    // Reflect change into Advanced JSON if it is currently valid (or empty)
    const parsed = safeParseJson(advancedJson);
    if (parsed.ok) {
      const obj = { ...parsed.obj };
      if (isDelete) delete obj[key]; else obj[key] = nextVal;
      setAdvancedJson(JSON.stringify(obj, null, 2));
      setAdvancedJsonError(null);
    }
  };

  const handleAdvancedJsonChange = (text) => {
    setAdvancedJson(text);
    const parsed = safeParseJson(text);
    if (!parsed.ok) {
      setAdvancedJsonError('Invalid JSON');
      return;
    }
    setAdvancedJsonError(null);
    const obj = parsed.obj || {};
    // Project parsed object onto visible param keys so typed controls mirror JSON
    const nextOverrides = {};
    visibleParamKeys.forEach((k) => {
      if (Object.prototype.hasOwnProperty.call(obj, k)) {
        nextOverrides[k] = obj[k];
      }
    });
    setParamOverrides(nextOverrides);
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        {submitError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {submitError}
          </Alert>
        )}
        <Grid container spacing={2} sx={{ mt: 1 }}>
          <Grid item xs={12}>
            <TextField
              fullWidth
              label="Configuration Name"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </Grid>
          <Grid item xs={12}>
            <TextField
              fullWidth
              label="Description"
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              multiline
              rows={2}
            />
          </Grid>
          <Grid item xs={12} sm={6}>
            <FormControl fullWidth>
              <InputLabel>LLM Provider</InputLabel>
              <Select
                value={formData.llm_provider_id}
                onChange={(e) => setFormData({ ...formData, llm_provider_id: e.target.value, model_name: '' })}
                label="LLM Provider"
              >
                {providers.map((provider) => (
                  <MenuItem key={provider.id} value={provider.id}>
                    {provider.name} ({provider.provider_type})
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12} sm={6}>
            <FormControl fullWidth disabled={!formData.llm_provider_id}>
              <InputLabel>Model</InputLabel>
              <Select
                value={formData.model_name}
                onChange={(e) => setFormData({ ...formData, model_name: e.target.value })}
                label="Model"
              >
                {models.map((model) => (
                  <MenuItem key={model.id} value={model.model_name}>
                    {model.display_name || model.model_name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12}>
            <FormControl fullWidth>
              <InputLabel>Prompt (Optional)</InputLabel>
              <Select
                value={formData.prompt_id || ''}
                onChange={(e) => setFormData({ ...formData, prompt_id: e.target.value || null })}
                label="Prompt (Optional)"
                disabled={promptsLoading}
              >
                <MenuItem value="">
                  <em>{promptsLoading ? 'Loading prompts...' : 'No Prompt'}</em>
                </MenuItem>
                {prompts.filter((p) => p.entity_type === 'llm_model').map((prompt) => (
                  <MenuItem key={prompt.id} value={prompt.id}>
                    {prompt.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>

          <Grid item xs={12}>
            <Autocomplete
              multiple
              options={knowledgeBases}
              getOptionLabel={(option) => option.name}
              value={selectedKnowledgeBases}
              onChange={(_, newValue) => {
                setFormData({ ...formData, knowledge_base_ids: newValue.map((kb) => kb.id) });
              }}
              disabled={knowledgeBasesLoading}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Knowledge Bases (Optional)"
                  placeholder={knowledgeBasesLoading ? 'Loading knowledge bases...' : 'Select knowledge bases for RAG'}
                />
              )}
            />
          </Grid>

          {/* KB Prompt Assignments */}
          {formData.knowledge_base_ids.length > 0 && (
            <Grid item xs={12}>
              <Paper sx={{ p: 2, mt: 1 }}>
                <Typography variant="h6" gutterBottom>
                  Knowledge Base Prompts (Optional)
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Assign specific KB context prompts to individual knowledge bases. If not specified, a default RAG prompt will be used.
                </Typography>

                {formData.knowledge_base_ids.map((kbId) => {
                  const kb = knowledgeBases.find((k) => k.id === kbId);
                  const currentAssignment = formData.kb_prompt_assignments.find((a) => a.knowledge_base_id === kbId);

                  return (
                    <Box key={kbId} sx={{ mb: 2, p: 1, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
                      <Typography variant="subtitle2" sx={{ mb: 1 }}>
                        {kb?.name || 'Unknown KB'}
                      </Typography>
                      <FormControl fullWidth size="small">
                        <InputLabel>Specific Prompt (Optional)</InputLabel>
                        <Select
                          value={currentAssignment?.prompt_id || ''}
                          onChange={(e) => {
                            const newAssignments = formData.kb_prompt_assignments.filter((a) => a.knowledge_base_id !== kbId);
                            if (e.target.value) {
                              newAssignments.push({
                                knowledge_base_id: kbId,
                                prompt_id: e.target.value,
                              });
                            }
                            setFormData({ ...formData, kb_prompt_assignments: newAssignments });
                          }}
                        >
                          {prompts
                            .filter((p) => p.entity_type === 'knowledge_base')
                            .map((prompt) => (
                              <MenuItem key={prompt.id} value={prompt.id}>
                                {prompt.name}
                              </MenuItem>
                            ))}
                        </Select>
                      </FormControl>
                    </Box>
                  );
                })}
              </Paper>
            </Grid>
          )}

          {/* Advanced parameters accordion */}
          <Grid item xs={12}>
            <Accordion>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography>Advanced options</Typography>
              </AccordionSummary>
              <AccordionDetails>

                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  These fields change the way the model is called. Depending on provider and model, you may need to customize these fields.
                  Incorrect values may cause errors or unexpected behavior.
                </Typography>

                <Box sx={{ mb: 2 }}>
                  <Grid container spacing={2} sx={{ mt: 0 }}>
                    {visibleParams.map(([key, spec]) => (
                      <Grid item xs={12} key={key}>
                        <Box sx={{ mb: 1 }}>
                          <Typography variant="subtitle1">{spec.label || key}</Typography>
                          {spec.description ? (
                            <Typography variant="body2" color="text.secondary">{spec.description}</Typography>
                          ) : null}
                        </Box>
                        {spec.type === 'enum' ? (
                          <FormControl fullWidth size="small">
                            <InputLabel>{spec.label || key}</InputLabel>
                            <Select
                              value={
                                paramOverrides[key] === undefined || paramOverrides[key] === null
                                  ? ''
                                  : paramOverrides[key]
                              }
                              label={spec.label || key}
                              renderValue={(selected) => {
                                const label = optionLabelForValue(spec.options, selected);
                                if (label) return label;
                                if (selected && typeof selected === 'object') {
                                  try { return JSON.stringify(selected); } catch { return '[object]'; }
                                }
                                return selected;
                              }}
                              onChange={(e) => {
                                const selectedRaw = e.target.value;
                                if (selectedRaw === '' || selectedRaw === null) {
                                  handleParamChange(key, undefined, true);
                                  return;
                                }
                                const chosen = optionByValue(spec.options, selectedRaw);
                                handleParamChange(key, chosen?.value ?? selectedRaw);
                              }}
                            >
                              {(spec.options || []).map((opt, idx) => (
                                <MenuItem key={`${key}-opt-${idx}`} value={opt.value ?? opt}>
                                  {opt.label || String(opt.value ?? opt)}
                                </MenuItem>
                              ))}
                            </Select>
                            {(() => {
                              const chosen = optionByValue(spec.options, paramOverrides[key]);
                              if (!chosen) return null;
                              const baseVal = (paramOverrides[key] && typeof paramOverrides[key] === 'object')
                                ? paramOverrides[key]
                                : (typeof chosen.value === 'object' ? { ...chosen.value } : {});
                              const inputFields = chosen.input_fields || [];
                              const inputSchemaProps = (chosen.input_schema && chosen.input_schema.properties) || {};
                              if (!inputFields.length && !Object.keys(inputSchemaProps).length) return null;
                              return (
                                <Box sx={{ pl: 1, pt: 1 }}>
                                  {inputFields.map((field) => (
                                    <TextField
                                      key={`${key}-${field.path}`}
                                      size="small"
                                      label={field.label || field.path}
                                      fullWidth
                                      sx={{ mb: 1 }}
                                      value={
                                        (() => {
                                          const parts = field.path.split('.');
                                          let cur = baseVal;
                                          for (const p of parts) {
                                            if (cur == null) break;
                                            cur = cur[p];
                                          }
                                          return cur ?? '';
                                        })()
                                      }
                                      onChange={(e) => {
                                        const nextVal = setByPath(baseVal, field.path, e.target.value);
                                        handleParamChange(key, { ...(chosen.value || {}), ...nextVal });
                                      }}
                                    />
                                  ))}
                                  {Object.entries(inputSchemaProps).map(([propKey, propSpec]) =>
                                    renderSchemaField(
                                      propKey,
                                      propSpec,
                                      baseVal[propKey],
                                      (val) => {
                                        const nextVal = setByPath(baseVal, propKey, val);
                                        handleParamChange(key, { ...(chosen.value || {}), ...nextVal });
                                      }
                                    )
                                  )}
                                </Box>
                              );
                            })()}
                          </FormControl>
                        ) : spec.type === 'boolean' ? (
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <Switch
                              checked={paramOverrides[key] === true || paramOverrides[key] === false ? !!paramOverrides[key] : false}
                              onChange={(e) => {
                                const val = e.target.checked;
                                handleParamChange(key, val);
                              }}
                            />
                            <Typography variant="body2">{spec.label || key}</Typography>
                          </Box>
                        ) : spec.type === 'array' && Array.isArray(spec.options) ? (
                          <Box sx={{ pl: 1 }}>
                            {(spec.options || []).map((opt, idx) => {
                              const currentArr = Array.isArray(paramOverrides[key]) ? paramOverrides[key] : [];
                              const existingIdx = matchOptionIndex(currentArr, opt.value);
                              const checked = existingIdx >= 0;
                              const handleToggle = (checkedVal) => {
                                let nextArr = Array.isArray(paramOverrides[key]) ? [...paramOverrides[key]] : [];
                                if (checkedVal) {
                                  nextArr.push(deepClone(opt.value));
                                } else if (existingIdx >= 0) {
                                  nextArr.splice(existingIdx, 1);
                                }
                                updateParamValue(key, nextArr);
                              };
                              const handleInputFieldChange = (field, val) => {
                                if (existingIdx < 0) return;
                                const nextArr = Array.isArray(paramOverrides[key]) ? [...paramOverrides[key]] : [];
                                nextArr[existingIdx] = setByPath(nextArr[existingIdx], field.path, val);
                                updateParamValue(key, nextArr);
                              };
                              const handleSchemaJsonChange = (val) => {
                                if (existingIdx < 0) return;
                                const nextArr = Array.isArray(paramOverrides[key]) ? [...paramOverrides[key]] : [];
                                nextArr[existingIdx] = { ...nextArr[existingIdx], ...(val || {}) };
                                updateParamValue(key, nextArr);
                              };
                              return (
                                <Paper key={`${key}-opt-${idx}`} variant="outlined" sx={{ p: 1, mb: 1 }}>
                                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                    <Switch
                                      checked={checked}
                                      onChange={(e) => handleToggle(e.target.checked)}
                                    />
                                    <Typography variant="body2">{opt.label || String(opt.value)}</Typography>
                                  </Box>
                                  {checked && (opt.input_fields?.length || opt.input_schema) ? (
                                    <Box sx={{ pl: 1, pt: 1 }} onClick={(e) => e.stopPropagation()}>
                                      {(opt.input_fields || []).map((field) => (
                                        <TextField
                                          key={`${key}-${idx}-${field.path}`}
                                          size="small"
                                          label={field.label || field.path}
                                          fullWidth
                                          sx={{ mb: 1 }}
                                          value={
                                            existingIdx >= 0
                                              ? (() => {
                                                const parts = field.path.split('.');
                                                let cur = paramOverrides[key][existingIdx];
                                                for (const p of parts) {
                                                  if (cur == null) break;
                                                  cur = cur[p];
                                                }
                                                return cur ?? '';
                                              })()
                                              : ''
                                          }
                                          onChange={(e) => handleInputFieldChange(field, e.target.value)}
                                        />
                                      ))}
                                      {opt.input_schema ? (
                                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                                          {Object.entries(opt.input_schema.properties || {}).map(([propKey, propSpec]) =>
                                            renderSchemaField(
                                              propKey,
                                              propSpec,
                                              existingIdx >= 0 ? paramOverrides[key][existingIdx]?.[propKey] : undefined,
                                              (val) => {
                                                if (existingIdx < 0) return;
                                                const nextArr = Array.isArray(paramOverrides[key]) ? [...paramOverrides[key]] : [];
                                                nextArr[existingIdx] = setByPath(nextArr[existingIdx], propKey, val);
                                                updateParamValue(key, nextArr);
                                              }
                                            )
                                          )}
                                        </Box>
                                      ) : null}
                                    </Box>
                                  ) : null}
                                </Paper>
                              );
                            })}
                          </Box>
                        ) : spec.type === 'array' && spec.items ? (
                          renderArrayWithItems(
                            key,
                            spec,
                            paramOverrides[key],
                            (val) => handleParamChange(key, val)
                          )
                        ) : spec.type === 'object' && spec.properties ? (
                          <Box sx={{ pl: 1 }}>
                            {Object.entries(spec.properties || {}).map(([propKey, propSpec]) =>
                              renderSchemaField(
                                propKey,
                                propSpec,
                                (paramOverrides[key] || {})[propKey],
                                (val) => {
                                  const base = (paramOverrides[key] && typeof paramOverrides[key] === 'object') ? { ...paramOverrides[key] } : {};
                                  const nextObj = setByPath(base, propKey, val);
                                  updateParamValue(key, nextObj);
                                }
                              )
                            )}
                          </Box>
                        ) : spec.options && spec.options.length ? (
                          <FormControl fullWidth size="small">
                            <InputLabel>{spec.label || key}</InputLabel>
                            <Select
                              value={
                                paramOverrides[key] === undefined || paramOverrides[key] === null
                                  ? ''
                                  : paramOverrides[key]
                              }
                              label={spec.label || key}
                              renderValue={(selected) => {
                                const label = optionLabelForValue(spec.options, selected);
                                if (label) return label;
                                if (selected && typeof selected === 'object') {
                                  try { return JSON.stringify(selected); } catch { return '[object]'; }
                                }
                                return selected;
                              }}
                              onChange={(e) => {
                                const selectedRaw = e.target.value;
                                if (selectedRaw === '' || selectedRaw === null) {
                                  handleParamChange(key, undefined, true);
                                  return;
                                }
                                const chosen = optionByValue(spec.options, selectedRaw);
                                handleParamChange(key, chosen?.value ?? selectedRaw);
                              }}
                            >
                              {(spec.options || []).map((opt, idx) => (
                                <MenuItem key={`${key}-opt-${idx}`} value={opt.value}>
                                  {opt.label || String(opt.value)}
                                </MenuItem>
                              ))}
                            </Select>
                            {(() => {
                              const chosen = optionByValue(spec.options, paramOverrides[key]);
                              if (!chosen) return null;
                              const baseVal = (paramOverrides[key] && typeof paramOverrides[key] === 'object') ? paramOverrides[key] : {};
                              const inputFields = chosen.input_fields || [];
                              const inputSchemaProps = (chosen.input_schema && chosen.input_schema.properties) || {};
                              if (!inputFields.length && !Object.keys(inputSchemaProps).length) return null;
                              return (
                                <Box sx={{ pl: 1, pt: 1 }}>
                                  {inputFields.map((field) => (
                                    <TextField
                                      key={`${key}-${field.path}`}
                                      size="small"
                                      label={field.label || field.path}
                                      fullWidth
                                      sx={{ mb: 1 }}
                                      value={
                                        (() => {
                                          const parts = field.path.split('.');
                                          let cur = baseVal;
                                          for (const p of parts) {
                                            if (cur == null) break;
                                            cur = cur[p];
                                          }
                                          return cur ?? '';
                                        })()
                                      }
                                      onChange={(e) => {
                                        const nextVal = setByPath(baseVal, field.path, e.target.value);
                                        handleParamChange(key, { ...(chosen.value || {}), ...nextVal });
                                      }}
                                    />
                                  ))}
                                  {Object.entries(inputSchemaProps).map(([propKey, propSpec]) =>
                                    renderSchemaField(
                                      propKey,
                                      propSpec,
                                      baseVal[propKey],
                                      (val) => {
                                        const nextVal = setByPath(baseVal, propKey, val);
                                        handleParamChange(key, { ...(chosen.value || {}), ...nextVal });
                                      }
                                    )
                                  )}
                                </Box>
                              );
                            })()}
                          </FormControl>
                        ) : (
                          <TextField
                            fullWidth
                            size="small"
                            label={spec.label || key}
                            type={spec.type === 'number' || spec.type === 'integer' ? 'number' : 'text'}
                            value={parseTextFieldValue(key, spec)}
                            onChange={(e) => {
                              const raw = e.target.value;
                              if (raw === "") {
                                handleParamChange(key, undefined, true);
                                return;
                              }
                              let nextVal = raw;
                              if (spec.type === "number") {
                                const n = parseFloat(raw);
                                nextVal = Number.isNaN(n) ? raw : n;
                              } else if (spec.type === "integer") {
                                const n = parseInt(raw, 10);
                                nextVal = Number.isNaN(n) ? raw : n;
                              } else if (spec.type === "object" || spec.type === "array") {
                                try {
                                  nextVal = JSON.parse(raw);
                                } catch {
                                  nextVal = raw;
                                }
                              }
                              handleParamChange(key, nextVal);
                            }}
                          />
                        )}
                      </Grid>
                    ))}
                  </Grid>
                </Box>

                <Box>
                  <Typography variant="subtitle2">
                    Advanced JSON &nbsp;
                    <Tooltip title="JSON overrides beyond mapped parameters. This form accepts any valid JSON and will apply parameters dynamically during requests.">
                      <InfoOutlined fontSize="small" color="action" />
                    </Tooltip>
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    minRows={4}
                    placeholder={`{ "temperature": 0.7 }`}
                    value={advancedJson}
                    onChange={(e) => handleAdvancedJsonChange(e.target.value)}
                    error={!!advancedJsonError}
                    helperText={advancedJsonError || ''}
                  />
                </Box>
              </AccordionDetails>
            </Accordion>
          </Grid>

          <Grid item xs={12}>
            {/* Status Toggle */}
            <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>Status</Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
              <Switch
                checked={formData.is_active}
                onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
              />
              <Typography variant="body2">Active</Typography>
            </Box>

            {/* Model Capabilities */}
            {capabilityToggles.length > 0 && (
              <>
                <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>Capabilities</Typography>
                {capabilityToggles.map(({ funcKey, value, label }) => (
                  <Box key={funcKey} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Switch
                      checked={!!value}
                      onChange={(e) => setFormData({
                        ...formData,
                        functionalities: {
                          ...(formData.functionalities || {}),
                          [funcKey]: e.target.checked,
                        },
                      })}
                    />
                    <Typography variant="body2">{label}</Typography>
                  </Box>
                ))}
              </>
            )}

            {/* System Designations */}
            <Typography variant="subtitle2" sx={{ mt: 2, mb: 0.5, fontWeight: 600 }}>System Designations</Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              Only one model can be designated for each role at a time.
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Switch
                checked={formData.is_side_call_model || false}
                onChange={(e) => setFormData({ ...formData, is_side_call_model: e.target.checked })}
              />
              <Typography variant="body2">Side Call Model</Typography>
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Switch
                checked={formData.is_ocr_call_model || false}
                onChange={(e) => setFormData({ ...formData, is_ocr_call_model: e.target.checked })}
              />
              <Typography variant="body2">OCR Model (requires vision)</Typography>
            </Box>
          </Grid>
          <Grid item xs={12}>
            <Box sx={{ mt: 0.5 }}>
              <Typography variant="body2" color="text.secondary">
                <strong>Side Calls:</strong> Used for optimized LLM operations like prompt assistance and title generation.
              </Typography>
              <Typography variant="body2" color="text.secondary">
                <strong>OCR Model:</strong> Used for vision-based text extraction from PDFs and images during ingestion.
              </Typography>
            </Box>
          </Grid>

        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={() => {
          onSubmit();
          // Invalidate config queries if caller designations changed
          if (formData.is_side_call_model || formData.is_ocr_call_model) {
            handleSaveSuccess();
          }
        }} variant="contained" disabled={isSubmitting || !formData.name || !formData.llm_provider_id || !formData.model_name}>
          {submitLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ModelConfigurationDialog;
