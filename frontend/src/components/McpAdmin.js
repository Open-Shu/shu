import React, { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import RefreshIcon from '@mui/icons-material/Refresh';
import SyncIcon from '@mui/icons-material/Sync';
import HubIcon from '@mui/icons-material/Hub';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { extractDataFromResponse, formatError } from '../services/api';
import { mcpAPI } from '../services/mcpApi';
import McpConnectionDialog from './McpConnectionDialog';
import McpToolConfigPanel from './McpToolConfigPanel';
import PageHelpHeader from './PageHelpHeader';

const STATUS_COLORS = {
  connected: 'success',
  disconnected: 'default',
  degraded: 'warning',
  error: 'error',
};

const ConnectionCard = ({ connection, onSync, onEdit, onDelete, onToggleEnabled, isSyncing }) => {
  const statusColor = STATUS_COLORS[connection.status] || 'default';
  const toolCount = connection.tool_count || 0;

  return (
    <Card
      sx={{
        transition: 'all 0.2s ease-in-out',
        '&:hover': { boxShadow: 2, transform: 'translateY(-1px)' },
        opacity: connection.enabled ? 1 : 0.6,
      }}
    >
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                {connection.name}
              </Typography>
              <Chip label={connection.status} color={statusColor} size="small" />
              <Chip label={`${toolCount} tool${toolCount !== 1 ? 's' : ''}`} size="small" variant="outlined" />
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              {connection.url}
            </Typography>
            {connection.last_error && (
              <Typography variant="caption" color="error.main">
                Last error: {connection.last_error}
              </Typography>
            )}
          </Box>

          <Stack direction="row" alignItems="center" spacing={1}>
            <Tooltip title={connection.enabled ? 'Disable connection' : 'Enable connection'}>
              <Switch
                checked={connection.enabled}
                onChange={() => onToggleEnabled(connection.id, !connection.enabled)}
                size="small"
                aria-label={`Toggle ${connection.name}`}
              />
            </Tooltip>
            <Tooltip title="Edit connection">
              <IconButton onClick={() => onEdit(connection)} size="small" aria-label={`Edit ${connection.name}`}>
                <EditIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Sync tools from server">
              <span>
                <IconButton
                  onClick={() => onSync(connection.id)}
                  disabled={isSyncing}
                  size="small"
                  aria-label={`Sync ${connection.name}`}
                >
                  <SyncIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Delete connection">
              <IconButton
                onClick={() => onDelete(connection)}
                size="small"
                color="error"
                aria-label={`Delete ${connection.name}`}
              >
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
        </Stack>
        <McpToolConfigPanel connection={connection} />
      </CardContent>
    </Card>
  );
};

const McpAdmin = () => {
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingConnection, setEditingConnection] = useState(null);
  const [syncingId, setSyncingId] = useState(null);

  const { data, isLoading, error, isFetching, refetch } = useQuery(
    ['mcp', 'connections'],
    () => mcpAPI.listConnections().then(extractDataFromResponse),
    { refetchOnWindowFocus: false }
  );

  const connections = data?.items || [];

  const deleteMut = useMutation((id) => mcpAPI.deleteConnection(id).then(extractDataFromResponse), {
    onSuccess: () => qc.invalidateQueries(['mcp', 'connections']),
    onError: (err) => {
      const detail = err.response?.data?.error;
      if (err.response?.status === 409 && detail?.details?.feed_ids) {
        const feedIds = detail.details.feed_ids;
        window.alert(
          `Cannot delete: ${feedIds.length} active feed(s) reference this connection.\n\nFeed IDs: ${feedIds.join(', ')}`
        );
      } else {
        window.alert(`Delete failed: ${formatError(err)}`);
      }
    },
  });

  const syncMut = useMutation((id) => mcpAPI.syncConnection(id).then(extractDataFromResponse), {
    onSuccess: () => {
      qc.invalidateQueries(['mcp', 'connections']);
      setSyncingId(null);
    },
    onError: (err) => {
      setSyncingId(null);
      window.alert(`Sync failed: ${formatError(err)}`);
    },
  });

  const handleSync = (id) => {
    setSyncingId(id);
    syncMut.mutate(id);
  };

  const handleToggleEnabled = (id, enabled) => {
    mcpAPI.updateConnection(id, { enabled }).then(() => qc.invalidateQueries(['mcp', 'connections']));
  };

  const handleEdit = (connection) => {
    setEditingConnection(connection);
    setDialogOpen(true);
  };

  const handleDelete = (connection) => {
    if (!window.confirm(`Delete MCP connection "${connection.name}"? This cannot be undone.`)) {
      return;
    }
    deleteMut.mutate(connection.id);
  };

  return (
    <Box p={3}>
      <PageHelpHeader
        title="MCP Connections"
        description="Connect external MCP servers to expose their tools as Shu plugins. Tools can be used in chat or scheduled as ingest feeds."
        icon={<HubIcon />}
        tips={[
          'Add a connection, then click Sync to discover tools from the server',
          'Tools default to chat-callable; switch to ingest type to use them in feeds',
          'HTTPS is required for remote servers; localhost allows plain HTTP',
        ]}
      />

      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={3}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          {connections.length} connection{connections.length !== 1 ? 's' : ''}
        </Typography>
        <Stack direction="row" spacing={1}>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => {
              setEditingConnection(null);
              setDialogOpen(true);
            }}
          >
            Add Connection
          </Button>
          <Tooltip title="Refresh connection list">
            <span>
              <Button variant="outlined" startIcon={<RefreshIcon />} onClick={() => refetch()} disabled={isFetching}>
                Refresh
              </Button>
            </span>
          </Tooltip>
        </Stack>
      </Stack>

      {isLoading && (
        <Box display="flex" alignItems="center" justifyContent="center" py={8}>
          <Stack alignItems="center" spacing={2}>
            <CircularProgress size={40} />
            <Typography variant="body2" color="text.secondary">
              Loading connections...
            </Typography>
          </Stack>
        </Box>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {formatError(error)}
        </Alert>
      )}

      {!isLoading && !error && (
        <Stack spacing={2}>
          {connections.length === 0 ? (
            <Box
              sx={{
                textAlign: 'center',
                py: 8,
                bgcolor: 'grey.50',
                borderRadius: 2,
                border: '1px dashed',
                borderColor: 'grey.300',
              }}
            >
              <Typography variant="h6" color="text.secondary" gutterBottom>
                No MCP connections
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Add a connection to an MCP server to get started
              </Typography>
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={() => {
                  setEditingConnection(null);
                  setDialogOpen(true);
                }}
              >
                Add Connection
              </Button>
            </Box>
          ) : (
            connections.map((conn) => (
              <ConnectionCard
                key={conn.id}
                connection={conn}
                onSync={handleSync}
                onEdit={handleEdit}
                onDelete={handleDelete}
                onToggleEnabled={handleToggleEnabled}
                isSyncing={syncingId === conn.id}
              />
            ))
          )}
        </Stack>
      )}

      <McpConnectionDialog
        open={dialogOpen}
        onClose={() => {
          setDialogOpen(false);
          setEditingConnection(null);
        }}
        connection={editingConnection}
      />
    </Box>
  );
};

export default McpAdmin;
