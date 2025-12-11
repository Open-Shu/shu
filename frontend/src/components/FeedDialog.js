import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Grid,
  TextField,
  Typography,
  Select,
  MenuItem,
  InputLabel,
  FormControl,
  Alert,
} from '@mui/material';
import { useMutation, useQuery, useQueryClient } from 'react-query';
import { extractDataFromResponse, extractItemsFromResponse, formatError, authAPI } from '../services/api';
import { pluginsAPI } from '../services/pluginsApi';
import { knowledgeBaseAPI } from '../services/api';
import { schedulesAPI } from '../services/schedulesApi';
import SchemaForm, { buildDefaultValues } from './SchemaForm';
import ProviderAuthPanel, { authGateDisabled } from './ProviderAuthPanel';
import { pluginPrimaryLabel } from '../utils/plugins';
import { useAuth } from '../hooks/useAuth';

/*
  Unified dialog for creating and editing Plugin Feeds.
  Props (union of previous dialogs):
    - mode: 'create' | 'edit'
    - open, onClose
    - schedule (for edit)
    - onCreated (for create)
    - presetPlugin (optional for create)
    - lockedKbId (optional for create)
*/
export default function FeedDialog({
  mode = 'create',
  open,
  onClose,
  schedule: scheduleProp = null,
  onCreated = null,
  presetPlugin = null,
  lockedKbId = null,
}) {
  const isEdit = mode === 'edit';
  const qc = useQueryClient();

  // Fetch fresh schedule data when dialog opens in edit mode to avoid stale cache
  // Only create query key with valid ID to prevent undefined values in cache
  const scheduleId = scheduleProp?.id;
  const scheduleQuery = useQuery(
    scheduleId ? ['schedule', 'detail', scheduleId] : ['schedule', 'detail', 'none'],
    () => schedulesAPI.get(scheduleId).then(extractDataFromResponse),
    { enabled: open && isEdit && !!scheduleId, staleTime: 0 }
  );
  // Use fresh data if available, fall back to prop for initial render
  const schedule = (isEdit && scheduleQuery.data) ? scheduleQuery.data : scheduleProp;

  // Core state shared across modes
  const [name, setName] = useState('');
  const [agentKey, setAgentKey] = useState('');
  const [interval, setInterval] = useState(3600);
  const [kbId, setKbId] = useState(lockedKbId || '');
  const [pluginName, setPluginName] = useState(presetPlugin || (schedule?.plugin_name || ''));
  const [schema, setSchema] = useState(null);
  const [values, setValues] = useState({});
  const [identitiesOk, setIdentitiesOk] = useState(true);
  const [ocrMode, setOcrMode] = useState('auto');
  const [authOverlay, setAuthOverlay] = useState({});
  const [devDiagnostics, setDevDiagnostics] = useState(false);
  const [resetCursor, setResetCursor] = useState(false);
  // Sync devDiagnostics and resetCursor when schedule loads (for edit mode)
  // For reset_cursor: always sync from params (including when absent/cleared by backend)
  React.useEffect(() => {
    if (isEdit && schedule?.params) {
      setDevDiagnostics(!!schedule.params.debug);
      setResetCursor(!!schedule.params.reset_cursor);
    }
  }, [isEdit, schedule]);
  // Owner & users
  const { user: currentUser } = useAuth();
  const [ownerUserId, setOwnerUserId] = useState('');
  const usersQ = useQuery(['users','list'], () => authAPI.getUsers().then(extractDataFromResponse), { enabled: open, staleTime: 10000 });
  const users = Array.isArray(usersQ.data) ? usersQ.data : [];
  const userOptions = users.map(u => ({ id: u.user_id || u.id, label: (u.email || u.name || u.user_id || u.id) }));



  // Load lists used by both modes
  const pluginsQ = useQuery(['plugins','list'], () => pluginsAPI.list().then(extractDataFromResponse), { enabled: open, staleTime: 10000 });
  const kbQ = useQuery(['kbs','list'], () => knowledgeBaseAPI.list().then(extractItemsFromResponse), { enabled: open && !lockedKbId, staleTime: 10000 });
  const kbLockedQ = useQuery(['kb','detail', lockedKbId], () => knowledgeBaseAPI.get(lockedKbId).then(extractDataFromResponse), { enabled: open && !!lockedKbId });

  const plugins = useMemo(() => Array.isArray(pluginsQ.data) ? pluginsQ.data : [], [pluginsQ.data]);
  const kbs = Array.isArray(kbQ.data) ? kbQ.data : [];

  const selectedPlugin = useMemo(() => {
    const name = isEdit ? (schedule?.plugin_name || '') : pluginName;
    return plugins.find(t => t.name === name) || null;
  }, [plugins, pluginName, schedule, isEdit]);

  // Mode-specific initialization
  useEffect(() => {
    if (!open) return;
    if (isEdit) {
      setName(schedule?.name || '');
      setAgentKey(schedule?.agent_key || '');
      setInterval(Number(schedule?.interval_seconds || 3600));
      setKbId(schedule?.params?.kb_id || '');
      setValues(schedule?.params || {});
      setOwnerUserId(schedule?.owner_user_id || '');
      try {
        const m = schedule?.params?.__host?.ocr?.mode || schedule?.params?.__host?.ocr_mode || 'auto';
        setOcrMode(String(m));
      } catch (_) { setOcrMode('auto'); }
    } else {
      setName('');
      setAgentKey('');
      setInterval(3600);
      setKbId(lockedKbId || '');
      setPluginName(presetPlugin || '');
      setValues({});
      setOcrMode('auto');
      try {
        const uid = (currentUser && (currentUser.user_id || currentUser.id)) || '';
        setOwnerUserId(uid);
      } catch (_) { setOwnerUserId(''); }
    }
    setSchema(null);
    setIdentitiesOk(true);
  }, [open, isEdit, schedule, lockedKbId, presetPlugin, currentUser]);
  // Select a sensible default plugin when opening create mode and none chosen yet
  useEffect(() => {
    if (!open || isEdit) return;
    if (pluginName) return;
    const first = plugins.find(t => Array.isArray(t.allowed_feed_ops) && t.allowed_feed_ops.length > 0);
    if (first) setPluginName(first.name);
  }, [open, isEdit, pluginName, plugins]);


  // Identity gating derived from op_auth (provider-agnostic)
  const currentOp = useMemo(() => String((values?.op || selectedPlugin?.default_feed_op || '')).toLowerCase(), [values, selectedPlugin]);

  // Schema + defaults merge
  useEffect(() => {
    if (!selectedPlugin) return;
    if (selectedPlugin.input_schema) {
      const defaults = buildDefaultValues(selectedPlugin.input_schema) || {};
      let merged = isEdit ? { ...defaults, ...(schedule?.params || {}) } : defaults;
      try {
        const allowedFeedOps = Array.isArray(selectedPlugin?.allowed_feed_ops) ? selectedPlugin.allowed_feed_ops : [];
        const defaultFeedOp = selectedPlugin?.default_feed_op || null;
        if (allowedFeedOps.length > 0 && !merged.op && defaultFeedOp) {
          merged = { ...merged, op: defaultFeedOp };
        }
      } catch (_) {}
      setSchema(selectedPlugin.input_schema);
      setValues(merged);
    } else {
      setSchema(null);
      setValues(isEdit ? (schedule?.params || {}) : {});
    }
    setIdentitiesOk(!(Array.isArray(selectedPlugin?.required_identities) && selectedPlugin.required_identities.length > 0));
  }, [selectedPlugin, isEdit, schedule]);

  // OCR section visibility
  const defaultFeedOp = selectedPlugin?.default_feed_op || null;
  const opForOcr = useMemo(() => String((values?.op || defaultFeedOp || '')).toLowerCase(), [values, defaultFeedOp]);
  const hasOcrCap = useMemo(() => Array.isArray(selectedPlugin?.capabilities) && selectedPlugin.capabilities.includes('ocr'), [selectedPlugin]);
  const showOcr = hasOcrCap && opForOcr === 'ingest';

  const onChangeField = (key, type, val) => {
    setValues(prev => {
      const next = { ...prev };
      if (type === 'number' || type === 'integer') next[key] = Number(val) || 0;
      else if (type === 'boolean') next[key] = !!val;
      else next[key] = val;
      return next;
    });
  };


  // Mutations
  const createMut = useMutation(
    (payload) => schedulesAPI.create(payload).then(extractDataFromResponse),
    {
      onSuccess: () => {
        qc.invalidateQueries(['schedules','list']);
        if (onCreated) onCreated();
        if (onClose) onClose();
      }
    }
  );
  const updateMut = useMutation(
    (payload) => schedulesAPI.update(schedule.id, payload).then(extractDataFromResponse),
    {
      onSuccess: () => {
        qc.invalidateQueries(['schedules','list']);
        if (onClose) onClose();
      }
    }
  );

  const handleSubmit = () => {
    if (!name) return;
    const tName = isEdit ? (schedule?.plugin_name || '') : pluginName;
    if (!tName) return;
    const effectiveKb = isEdit ? (kbId || values.kb_id) : (lockedKbId || kbId);
    if (!effectiveKb) return;

    let baseParams = { ...(values || {}), ...(isEdit ? (kbId ? { kb_id: kbId } : {}) : { kb_id: effectiveKb }) };
    // Merge host overlays: OCR + auth overlay
    let overlay = { ...(baseParams.__host || {}) };
    if (showOcr) overlay = { ...overlay, ocr: { mode: ocrMode } };
    if (authOverlay && Object.keys(authOverlay).length > 0) overlay = { ...overlay, ...authOverlay };
    if (Object.keys(overlay).length > 0) baseParams.__host = overlay;
    // Dev-only diagnostics and reset cursor
    const isDev = String(process.env.NODE_ENV) === 'development';
    if (isDev && devDiagnostics) baseParams.debug = true;
    if (resetCursor) {
      const ok = window.confirm('Reset cursor: This will clear the incremental sync checkpoint and force a full rescan on next run. This may re-ingest documents and increase API usage. Proceed?');
      if (!ok) {
        // Revert the toggle
        setResetCursor(false);
      } else {
        baseParams.reset_cursor = true;
      }
    }

    const payload = isEdit ? {
      name,
      agent_key: agentKey || null,
      interval_seconds: Number(interval) || 3600,
      params: baseParams,
      owner_user_id: ownerUserId || null,
    } : {
      name,
      plugin_name: tName,
      params: baseParams,
      interval_seconds: Number(interval) || 3600,
      agent_key: agentKey || null,
      enabled: true,
      owner_user_id: ownerUserId || null,
    };

    if (isEdit) updateMut.mutate(payload); else createMut.mutate(payload);
  };

  const error = isEdit ? updateMut.error : createMut.error;
  const isLoading = isEdit ? updateMut.isLoading : createMut.isLoading;

  const renderSchemaForm = useMemo(() => {
    if (!schema || !schema.properties) return (
      <Alert severity="info">This plugin has no declared input schema{isEdit ? '' : '. The feed will pass only kb_id and any defaults.'}</Alert>
    );
    const allowedOps = Array.isArray(selectedPlugin?.allowed_feed_ops) ? selectedPlugin.allowed_feed_ops : [];
    const hideOp = allowedOps.length === 1; // lock op when only one feed-safe op
    const hideKeys = new Set(['kb_id', ...(hideOp ? ['op'] : [])]);
    return (
      <SchemaForm
        schema={schema}
        values={values}
        onChangeField={onChangeField}
        hideKeys={new Set([...hideKeys, 'debug', 'reset_cursor'])}
      />
    );
  }, [schema, values, selectedPlugin, isEdit]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{isEdit ? 'Edit Plugin Feed' : 'Create Plugin Feed'}</DialogTitle>
      <DialogContent dividers>
        {(isEdit ? updateMut.isError : createMut.isError) && (
          <Alert severity="error" sx={{ mb: 2 }}>{formatError(error)}</Alert>
        )}
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <TextField fullWidth label="Feed Name" value={name} onChange={(e) => setName(e.target.value)} />
          </Grid>
          <Grid item xs={12} md={6}>
            <TextField fullWidth label="Agent Key (optional)" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} />
          </Grid>
          {!isEdit && (
            <Grid item xs={12} md={6}>
              <FormControl fullWidth>
                <InputLabel id="plugin-label">Plugin</InputLabel>
                <Select labelId="plugin-label" label="Plugin" value={pluginName} onChange={(e) => setPluginName(e.target.value)}>
                  {plugins
                    .filter(t => Array.isArray(t.allowed_feed_ops) && t.allowed_feed_ops.length > 0)
                    .map((t) => (
                      <MenuItem key={t.name} value={t.name}>{pluginPrimaryLabel(t)}</MenuItem>
                    ))}
                </Select>
              </FormControl>
            </Grid>
          )}

          <Grid item xs={12} md={6}>
            {lockedKbId && !isEdit ? (
              <TextField fullWidth label="Knowledge Base" value={(kbLockedQ.data && (kbLockedQ.data.name || kbLockedQ.data.id)) || lockedKbId} disabled />
            ) : (
              <FormControl fullWidth>
                <InputLabel id="kb-label">Knowledge Base</InputLabel>
                <Select labelId="kb-label" label="Knowledge Base" value={isEdit ? (kbId || values.kb_id || '') : kbId} onChange={(e) => setKbId(e.target.value)}>
                  {kbs.map((kb) => (
                    <MenuItem key={kb.id} value={kb.id}>{kb.name || kb.id}</MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
          </Grid>
          <Grid item xs={12} md={6}>
            <FormControl fullWidth>
              <InputLabel id="owner-label">Owner</InputLabel>
              <Select
                labelId="owner-label"
                label="Owner"
                value={ownerUserId}
                onChange={(e) => setOwnerUserId(e.target.value)}
                renderValue={(val) => {
                  if (!val) return 'Unassigned';
                  const opt = userOptions.find(o => String(o.id) === String(val));
                  return opt ? opt.label : val;
                }}
              >
                <MenuItem value=""><em>Unassigned</em></MenuItem>
                {userOptions.map((o) => (
                  <MenuItem key={o.id} value={o.id}>{o.label}</MenuItem>
                ))}
                {!userOptions.some(o => String(o.id) === String(ownerUserId)) && ownerUserId && (
                  <MenuItem value={ownerUserId}>{ownerUserId}</MenuItem>
                )}
              </Select>
            </FormControl>
          </Grid>

          <Grid item xs={12} md={6}>
            <FormControl fullWidth>
              <InputLabel id="interval-label">Interval</InputLabel>


              <Select labelId="interval-label" label="Interval" value={interval} onChange={(e) => setInterval(Number(e.target.value))}>
                <MenuItem value={900}>Every 15 minutes</MenuItem>
                <MenuItem value={3600}>Hourly</MenuItem>
                <MenuItem value={21600}>Every 6 hours</MenuItem>
                <MenuItem value={86400}>Daily</MenuItem>
              </Select>
            </FormControl>
          </Grid>
        </Grid>

        <ProviderAuthPanel
          plugin={selectedPlugin}
          op={currentOp}
          pluginName={isEdit ? (schedule?.plugin_name || '') : pluginName}
          initialOverlay={isEdit ? (schedule?.params?.__host || null) : null}
          onGateChange={(ok) => setIdentitiesOk(!!ok)}
          onAuthOverlayChange={(ov) => setAuthOverlay(ov || {})}
        />

        <Box mt={3}>
          <Typography variant="subtitle1" gutterBottom>Plugin Parameters</Typography>
          {renderSchemaForm}
            {/* Developer diagnostics and reset cursor controls */}
            <Box mt={2}>
              {String(process.env.NODE_ENV) === 'development' && (
                <FormControlLabel
                  control={<Checkbox checked={devDiagnostics} onChange={(e) => setDevDiagnostics(e.target.checked)} />}
                  label="Include diagnostics in execution result (development)"
                />
              )}
              <FormControlLabel
                control={<Checkbox checked={resetCursor} onChange={(e) => setResetCursor(e.target.checked)} />}
                label="Reset cursor on next run (forces full rescan)"
              />
              <Typography variant="caption" color="text.secondary">
                Resetting the cursor clears the incremental checkpoint for this feed and KB. The next run will perform a full discovery and may re-ingest existing documents.
              </Typography>
            </Box>

          {showOcr && (
            <Box mt={2}>
              <Typography variant="subtitle1" gutterBottom>OCR Settings</Typography>
              <FormControl fullWidth>
                <InputLabel id="ocr-mode-label">OCR Mode</InputLabel>
                <Select labelId="ocr-mode-label" label="OCR Mode" value={ocrMode} onChange={(e) => setOcrMode(e.target.value)}>
                  <MenuItem value="always">Always — Always run OCR</MenuItem>
                  <MenuItem value="auto">Auto — OCR PDFs/images; skip text files</MenuItem>
                  <MenuItem value="fallback">Fallback — Try text first; OCR if empty</MenuItem>
                  <MenuItem value="never">Never — Do not run OCR</MenuItem>
                </Select>
              </FormControl>
              <Typography variant="caption" color="text.secondary">
                Applies only to this feed.
              </Typography>
            </Box>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={
            isLoading ||
            !name ||
            (!isEdit && !pluginName) ||
            !(isEdit ? (kbId || values.kb_id) : (lockedKbId || kbId)) ||
            authGateDisabled(selectedPlugin || {}, currentOp, identitiesOk)
          }
        >
          {isEdit ? 'Save' : 'Create Feed'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

