import React, { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import { useMutation, useQueryClient } from 'react-query';
import { extractDataFromResponse, formatError } from '../services/api';
import { mcpAPI } from '../services/mcpApi';

const EMPTY_HEADER = { key: '', value: '', masked: true };

const HeaderRow = ({ header, index, onChange, onRemove }) => {
  const [visible, setVisible] = useState(false);

  return (
    <Stack direction="row" spacing={1} alignItems="center">
      <TextField
        label="Header Name"
        value={header.key}
        onChange={(e) => onChange(index, { ...header, key: e.target.value })}
        size="small"
        sx={{ flex: 1 }}
        placeholder="Authorization"
      />
      <TextField
        label="Value"
        value={header.value}
        onChange={(e) => onChange(index, { ...header, value: e.target.value })}
        type={visible ? 'text' : 'password'}
        size="small"
        sx={{ flex: 2 }}
        placeholder="Bearer token..."
      />
      <Tooltip title={visible ? 'Hide value' : 'Show value'}>
        <IconButton
          onClick={() => setVisible(!visible)}
          size="small"
          aria-label={visible ? 'Hide header value' : 'Show header value'}
        >
          {visible ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
        </IconButton>
      </Tooltip>
      <Tooltip title="Remove header">
        <IconButton onClick={() => onRemove(index)} size="small" aria-label="Remove header">
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Stack>
  );
};

export default function McpConnectionDialog({ open, onClose, connection = null }) {
  const isEdit = !!connection;
  const qc = useQueryClient();

  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [headers, setHeaders] = useState([]);
  const [connectMs, setConnectMs] = useState('');
  const [callMs, setCallMs] = useState('');
  const [readMs, setReadMs] = useState('');
  const [responseSizeLimit, setResponseSizeLimit] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      if (connection) {
        setName(connection.name || '');
        setUrl(connection.url || '');
        setHeaders([]);
        setConnectMs(connection.timeouts?.connect_ms?.toString() || '');
        setCallMs(connection.timeouts?.call_ms?.toString() || '');
        setReadMs(connection.timeouts?.read_ms?.toString() || '');
        setResponseSizeLimit(connection.response_size_limit_bytes?.toString() || '');
        setEnabled(connection.enabled ?? true);
      } else {
        setName('');
        setUrl('');
        setHeaders([]);
        setConnectMs('');
        setCallMs('');
        setReadMs('');
        setResponseSizeLimit('');
        setEnabled(true);
      }
      setError(null);
    }
  }, [open, connection]);

  const createMut = useMutation((body) => mcpAPI.createConnection(body).then(extractDataFromResponse), {
    onSuccess: () => {
      qc.invalidateQueries(['mcp', 'connections']);
      onClose();
    },
    onError: (err) => setError(formatError(err)),
  });

  const updateMut = useMutation((body) => mcpAPI.updateConnection(connection.id, body).then(extractDataFromResponse), {
    onSuccess: () => {
      qc.invalidateQueries(['mcp', 'connections']);
      onClose();
    },
    onError: (err) => setError(formatError(err)),
  });

  const isSaving = createMut.isLoading || updateMut.isLoading;

  const handleHeaderChange = (index, updated) => {
    setHeaders((prev) => prev.map((h, i) => (i === index ? updated : h)));
  };

  const handleHeaderRemove = (index) => {
    setHeaders((prev) => prev.filter((_, i) => i !== index));
  };

  const handleAddHeader = () => {
    setHeaders((prev) => [...prev, { ...EMPTY_HEADER }]);
  };

  const buildPayload = () => {
    const payload = {};

    if (!isEdit || name !== connection.name) {
      payload.name = name.trim();
    }
    if (!isEdit || url !== connection.url) {
      payload.url = url.trim();
    }

    const nonEmptyHeaders = headers.filter((h) => h.key.trim() && h.value.trim());
    if (nonEmptyHeaders.length > 0) {
      payload.headers = {};
      nonEmptyHeaders.forEach((h) => {
        payload.headers[h.key.trim()] = h.value.trim();
      });
    }

    const timeouts = {};
    if (connectMs) {
      timeouts.connect_ms = parseInt(connectMs, 10);
    }
    if (callMs) {
      timeouts.call_ms = parseInt(callMs, 10);
    }
    if (readMs) {
      timeouts.read_ms = parseInt(readMs, 10);
    }
    if (Object.keys(timeouts).length > 0) {
      payload.timeouts = timeouts;
    }

    if (responseSizeLimit) {
      payload.response_size_limit_bytes = parseInt(responseSizeLimit, 10);
    }

    payload.enabled = enabled;

    return payload;
  };

  const handleSubmit = () => {
    setError(null);
    if (!name.trim()) {
      setError('Connection name is required');
      return;
    }
    if (!url.trim()) {
      setError('Server URL is required');
      return;
    }

    const payload = buildPayload();

    if (isEdit) {
      updateMut.mutate(payload);
    } else {
      createMut.mutate(payload);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? 'Edit MCP Connection' : 'Add MCP Connection'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}

          <TextField
            label="Connection Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            fullWidth
            required
            autoFocus={!isEdit}
            placeholder="e.g. confluence-wiki"
            helperText="Unique display name for this connection"
          />

          <TextField
            label="Server URL"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            fullWidth
            required
            placeholder="https://mcp-server.example.com/sse"
            helperText="Streamable HTTP endpoint. HTTPS required except for localhost."
          />

          <Box>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="subtitle2">Auth Headers</Typography>
              <Button size="small" startIcon={<AddIcon />} onClick={handleAddHeader} aria-label="Add auth header">
                Add Header
              </Button>
            </Stack>
            {headers.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No auth headers configured. Click &quot;Add Header&quot; to add credentials.
              </Typography>
            )}
            <Stack spacing={1}>
              {headers.map((header, i) => (
                <HeaderRow
                  key={i}
                  header={header}
                  index={i}
                  onChange={handleHeaderChange}
                  onRemove={handleHeaderRemove}
                />
              ))}
            </Stack>
            {isEdit && headers.length === 0 && (
              <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
                Existing headers are stored encrypted and not shown. Add new headers to replace them.
              </Typography>
            )}
          </Box>

          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Timeouts (ms)
            </Typography>
            <Stack direction="row" spacing={1}>
              <TextField
                label="Connect"
                value={connectMs}
                onChange={(e) => setConnectMs(e.target.value)}
                size="small"
                type="number"
                placeholder="5000"
                sx={{ flex: 1 }}
              />
              <TextField
                label="Call"
                value={callMs}
                onChange={(e) => setCallMs(e.target.value)}
                size="small"
                type="number"
                placeholder="30000"
                sx={{ flex: 1 }}
              />
              <TextField
                label="Read"
                value={readMs}
                onChange={(e) => setReadMs(e.target.value)}
                size="small"
                type="number"
                placeholder="30000"
                sx={{ flex: 1 }}
              />
            </Stack>
          </Box>

          <TextField
            label="Response Size Limit (bytes)"
            value={responseSizeLimit}
            onChange={(e) => setResponseSizeLimit(e.target.value)}
            type="number"
            size="small"
            placeholder="10485760"
            helperText="Maximum response size from MCP server (default 10MB)"
          />

          <FormControlLabel
            control={<Switch checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />}
            label="Enabled"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <Button onClick={handleSubmit} variant="contained" disabled={isSaving}>
          {isSaving ? 'Saving...' : isEdit ? 'Save Changes' : 'Add Connection'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
