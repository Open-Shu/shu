import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
  Alert,
  Switch,
  FormControlLabel,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Collapse,
} from '@mui/material';
import JSONPretty from 'react-json-pretty';
import { useMutation, useQuery } from 'react-query';
import { extractDataFromResponse, extractItemsFromResponse, formatError, knowledgeBaseAPI } from '../services/api';
import { pluginsAPI } from '../services/pluginsApi';
import SchemaForm, { buildDefaultValues } from './SchemaForm';
import ProviderAuthPanel, { authGateDisabled } from './ProviderAuthPanel';
import { pluginDisplayName } from '../utils/plugins';

export default function PluginExecutionModal({ open, onClose, plugin, onStart = null, onResult = null }) {
  const pluginDef = plugin;
  const availableOps = useMemo(() => Object.keys(pluginDef?.ops || {}), [pluginDef]);
  const [selectedOp, setSelectedOp] = useState('');
  const opSchema = useMemo(() => pluginDef?.ops?.[selectedOp]?.input_schema || null, [pluginDef, selectedOp]);
  const [schema, setSchema] = useState(null);
  const [values, setValues] = useState({});
  const [agentKey, setAgentKey] = useState('');
  const [rawMode, setRawMode] = useState(false);
  const [rawJson, setRawJson] = useState('');
  const [result, setResult] = useState(null);
  const [showErrorDetails, setShowErrorDetails] = useState(false);
  const [errorEnvelope, setErrorEnvelope] = useState(null);

  const [identitiesOk, setIdentitiesOk] = useState(true);
  const [authOverlay, setAuthOverlay] = useState({});

  const currentOp = useMemo(() => {
    if (rawMode) {
      try {
        const obj = JSON.parse(rawJson || '{}');
        return String(obj?.op || selectedOp || '').toLowerCase();
      } catch (_) {
        // Ignore error
      }
    }
    return String(selectedOp || '').toLowerCase();
  }, [rawMode, rawJson, selectedOp]);

  const hasKbId = useMemo(() => Boolean(schema?.properties?.kb_id), [schema]);
  const kbsQ = useQuery(['kbs', 'list'], () => knowledgeBaseAPI.list().then(extractItemsFromResponse), {
    enabled: hasKbId && !rawMode,
    staleTime: 10000,
  });

  useEffect(() => {
    const firstOp = availableOps.length > 0 ? availableOps[0] : '';
    setSelectedOp(firstOp);
    const firstSchema = pluginDef?.ops?.[firstOp]?.input_schema || null;
    setSchema(firstSchema);
    const defaults = buildDefaultValues(firstSchema);
    setValues(defaults);
    setRawJson(JSON.stringify(defaults, null, 2));
    setResult(null);
    setErrorEnvelope(null);
    setShowErrorDetails(false);
    setIdentitiesOk(true);
  }, [pluginDef, availableOps]);

  useEffect(() => {
    setSchema(opSchema);
    const defaults = buildDefaultValues(opSchema);
    setValues(defaults);
    setRawJson(JSON.stringify(defaults, null, 2));
  }, [opSchema]);

  const execMut = useMutation(
    ({ name, params, agentKey }) => pluginsAPI.execute(name, params, agentKey).then(extractDataFromResponse),
    {
      onSuccess: (data) => {
        setResult(data);
        setErrorEnvelope(null);
        setShowErrorDetails(false);
        try {
          if (typeof onResult === 'function') {
            onResult(data, { mode: 'run', plugin: pluginDef });
          }
        } catch {
          // Ignore error
        }
      },
      onError: (err) => {
        try {
          // Capture structured error envelope for details view
          setErrorEnvelope(
            err?.response?.data ?? {
              error: { message: err?.message || String(err) },
            }
          );
        } catch (_e) {
          setErrorEnvelope({
            error: { message: err?.message || 'Unknown error' },
          });
        }
      },
    }
  );
  const supportsPreview = useMemo(() => {
    const props = schema?.properties || {};
    return Boolean(props.preview || props.approve);
  }, [schema]);

  const previewMut = useMutation(
    ({ name, params, agentKey }) => pluginsAPI.execute(name, params, agentKey).then(extractDataFromResponse),
    {
      onSuccess: (data) => {
        setResult(data);
        setErrorEnvelope(null);
        setShowErrorDetails(false);
        try {
          if (typeof onResult === 'function') {
            onResult(data, { mode: 'preview', plugin: pluginDef });
          }
        } catch {
          // Ignore error
        }
      },
      onError: (err) => {
        try {
          setErrorEnvelope(
            err?.response?.data ?? {
              error: { message: err?.message || String(err) },
            }
          );
        } catch (_e) {
          setErrorEnvelope({
            error: { message: err?.message || 'Unknown error' },
          });
        }
      },
    }
  );

  const onChangeField = (key, type, val) => {
    setValues((prev) => {
      const next = { ...prev };
      if (type === 'number' || type === 'integer') {
        const parsed = Number(val);
        next[key] = Number.isNaN(parsed) ? 0 : parsed;
      } else if (type === 'boolean') {
        next[key] = !!val;
      } else {
        next[key] = val;
      }
      setRawJson(JSON.stringify(next, null, 2));
      return next;
    });
  };

  const renderForm = useMemo(() => {
    if (!schema || !schema.properties || rawMode) {
      return null;
    }
    return <SchemaForm schema={schema} values={values} onChangeField={onChangeField} hideKeys={new Set(['kb_id'])} />;
  }, [schema, values, rawMode]);

  const handleRun = () => {
    let params = rawMode ? null : { ...values, op: selectedOp };
    if (rawMode) {
      try {
        params = JSON.parse(rawJson || '{}');
      } catch (e) {
        alert('Invalid JSON');
        return;
      }
    }
    if (authOverlay && Object.keys(authOverlay).length > 0) {
      params = {
        ...params,
        __host: { ...(params.__host || {}), ...authOverlay },
      };
    }
    setShowErrorDetails(false);
    setErrorEnvelope(null);
    setResult(null);
    try {
      if (typeof onStart === 'function') {
        onStart({ mode: 'run', plugin: pluginDef, params });
      }
    } catch {
      // Ignore error
    }
    execMut.mutate({
      name: pluginDef?.name,
      params,
      agentKey: agentKey || null,
    });
  };

  const handlePreview = () => {
    let params = rawMode ? null : { ...values, op: selectedOp };
    if (rawMode) {
      try {
        params = JSON.parse(rawJson || '{}');
      } catch (e) {
        alert('Invalid JSON');
        return;
      }
    }
    if (authOverlay && Object.keys(authOverlay).length > 0) {
      params = {
        ...params,
        __host: { ...(params.__host || {}), ...authOverlay },
      };
    }
    params = { ...params, preview: true, approve: false };
    setShowErrorDetails(false);
    setErrorEnvelope(null);
    setResult(null);
    try {
      if (typeof onStart === 'function') {
        onStart({ mode: 'preview', plugin: pluginDef, params });
      }
    } catch {
      // Ignore error
    }
    previewMut.mutate({
      name: pluginDef?.name,
      params,
      agentKey: agentKey || null,
    });
  };

  const handleApproveRun = () => {
    let params = rawMode ? null : { ...values, op: selectedOp };
    if (rawMode) {
      try {
        params = JSON.parse(rawJson || '{}');
      } catch (e) {
        alert('Invalid JSON');
        return;
      }
    }
    if (authOverlay && Object.keys(authOverlay).length > 0) {
      params = {
        ...params,
        __host: { ...(params.__host || {}), ...authOverlay },
      };
    }
    params = { ...params, preview: false, approve: true };
    setShowErrorDetails(false);
    setErrorEnvelope(null);
    setResult(null);
    try {
      if (typeof onStart === 'function') {
        onStart({ mode: 'approve', plugin: pluginDef, params });
      }
    } catch {
      // Ignore error
    }
    execMut.mutate({
      name: pluginDef?.name,
      params,
      agentKey: agentKey || null,
    });
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Execute Plugin: {pluginDisplayName(pluginDef) || pluginDef?.name}</DialogTitle>
      <DialogContent dividers>
        {availableOps.length > 0 ? (
          <Typography variant="body2" color="text.secondary" mb={2}>
            Schema-based form. Toggle Raw JSON if needed.
          </Typography>
        ) : (
          <Alert severity="info" sx={{ mb: 2 }}>
            This plugin does not declare any operations. Use Raw JSON mode to provide params.
          </Alert>
        )}

        {availableOps.length > 1 && (
          <FormControl fullWidth size="small" sx={{ mb: 2 }}>
            <InputLabel id="exec-op-label">Operation</InputLabel>
            <Select
              labelId="exec-op-label"
              label="Operation"
              value={selectedOp}
              onChange={(e) => setSelectedOp(e.target.value)}
            >
              {availableOps.map((op) => (
                <MenuItem key={op} value={op}>
                  {pluginDef?.ops?.[op]?.title || op}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}

        <Box display="flex" alignItems="center" justifyContent="space-between" mb={2}>
          <FormControlLabel
            control={<Switch checked={rawMode} onChange={(e) => setRawMode(e.target.checked)} />}
            label="Raw JSON"
          />
          <TextField
            size="small"
            label="Agent Key (optional)"
            value={agentKey}
            onChange={(e) => setAgentKey(e.target.value)}
          />
        </Box>

        <ProviderAuthPanel
          plugin={pluginDef}
          op={currentOp}
          onGateChange={(ok) => setIdentitiesOk(!!ok)}
          onAuthOverlayChange={(ov) => setAuthOverlay(ov || {})}
        />

        {!rawMode && hasKbId && (
          <FormControl fullWidth size="small" sx={{ mb: 2 }}>
            <InputLabel id="kb-select-label">Knowledge Base</InputLabel>
            <Select
              labelId="kb-select-label"
              label="Knowledge Base"
              value={values?.kb_id || ''}
              onChange={(e) => {
                const val = e.target.value || '';
                setValues((prev) => {
                  const next = { ...(prev || {}), kb_id: val };
                  try {
                    setRawJson(JSON.stringify(next, null, 2));
                  } catch {
                    // Ignore error
                  }
                  return next;
                });
              }}
            >
              <MenuItem value="">
                <em>None</em>
              </MenuItem>
              {Array.isArray(kbsQ.data) &&
                kbsQ.data.map((kb) => (
                  <MenuItem key={kb.id} value={kb.id}>
                    {kb.name || kb.id}
                  </MenuItem>
                ))}
            </Select>
          </FormControl>
        )}

        {rawMode ? (
          <TextField fullWidth multiline minRows={10} value={rawJson} onChange={(e) => setRawJson(e.target.value)} />
        ) : (
          renderForm
        )}

        {execMut.isError && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {formatError(execMut.error)}
            <Box mt={1}>
              <Button size="small" onClick={() => setShowErrorDetails((s) => !s)}>
                {showErrorDetails ? 'Hide details' : 'Show details'}
              </Button>
            </Box>
            <Collapse in={showErrorDetails}>
              <Box
                mt={1}
                sx={{
                  bgcolor: '#f8fafc',
                  p: 1,
                  borderRadius: 1,
                  border: '1px solid #e2e8f0',
                }}
              >
                <JSONPretty data={errorEnvelope || {}} />
              </Box>
            </Collapse>
            {supportsPreview &&
              errorEnvelope?.error?.code === 'approval_required' &&
              errorEnvelope?.error?.details?.plan && (
                <Box mt={1}>
                  <Typography variant="subtitle2">Proposed action plan</Typography>
                  <Box
                    mt={1}
                    sx={{
                      bgcolor: '#f8fafc',
                      p: 1,
                      borderRadius: 1,
                      border: '1px solid #e2e8f0',
                    }}
                  >
                    <JSONPretty data={errorEnvelope.error.details.plan} />
                  </Box>
                  <Button
                    size="small"
                    variant="contained"
                    sx={{ mt: 1 }}
                    onClick={handleApproveRun}
                    disabled={execMut.isLoading || previewMut.isLoading}
                  >
                    Approve & Run
                  </Button>
                </Box>
              )}
          </Alert>
        )}

        {result && (
          <Box mt={2}>
            <Typography variant="h6">Result</Typography>
            <JSONPretty data={result} />
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        {supportsPreview ? (
          <>
            <Button
              onClick={handlePreview}
              disabled={
                execMut.isLoading || previewMut.isLoading || authGateDisabled(pluginDef, currentOp, identitiesOk)
              }
            >
              Preview
            </Button>
            <Button
              variant="contained"
              onClick={handleApproveRun}
              disabled={
                execMut.isLoading || previewMut.isLoading || authGateDisabled(pluginDef, currentOp, identitiesOk)
              }
            >
              Approve & Run
            </Button>
          </>
        ) : (
          <Button
            variant="contained"
            onClick={handleRun}
            disabled={execMut.isLoading || authGateDisabled(pluginDef, currentOp, identitiesOk)}
          >
            Run
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
