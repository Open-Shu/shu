import React, { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Collapse,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import SaveIcon from '@mui/icons-material/Save';
import { useMutation, useQueryClient } from 'react-query';
import { extractDataFromResponse, formatError } from '../services/api';
import { mcpAPI } from '../services/mcpApi';
import McpIngestConfigForm from './McpIngestConfigForm';

const ToolRow = ({ connectionId, toolName, description, config }) => {
  const qc = useQueryClient();
  const [type, setType] = useState(config?.type || 'chat_callable');
  const [enabled, setEnabled] = useState(config?.enabled ?? true);
  const [ingestConfig, setIngestConfig] = useState(config?.ingest || null);
  const [dirty, setDirty] = useState(false);

  const saveMut = useMutation(
    (payload) => mcpAPI.updateToolConfig(connectionId, toolName, payload).then(extractDataFromResponse),
    {
      onSuccess: () => {
        qc.invalidateQueries(['mcp', 'connections']);
        setDirty(false);
      },
    }
  );

  const handleTypeChange = (newType) => {
    setType(newType);
    setDirty(true);
    if (newType === 'ingest' && !ingestConfig) {
      setIngestConfig({
        method: 'text',
        field_mapping: { title: '', content: '', source_id: '' },
      });
    }
  };

  const handleEnabledChange = (newEnabled) => {
    setEnabled(newEnabled);
    setDirty(true);
  };

  const handleIngestChange = (newIngest) => {
    setIngestConfig(newIngest);
    setDirty(true);
  };

  const handleSave = () => {
    const payload = { type, enabled };
    if (type === 'ingest' && ingestConfig) {
      payload.ingest = ingestConfig;
    }
    saveMut.mutate(payload);
  };

  return (
    <Card variant="outlined" sx={{ mb: 1 }}>
      <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                {toolName}
              </Typography>
              <Chip label={type} size="small" color={type === 'ingest' ? 'secondary' : 'primary'} variant="outlined" />
            </Stack>
            {description && (
              <Typography variant="caption" color="text.secondary">
                {description}
              </Typography>
            )}
          </Box>

          <Stack direction="row" alignItems="center" spacing={1}>
            <FormControl size="small" sx={{ minWidth: 130 }}>
              <InputLabel>Type</InputLabel>
              <Select value={type} label="Type" onChange={(e) => handleTypeChange(e.target.value)}>
                <MenuItem value="chat_callable">Chat Callable</MenuItem>
                <MenuItem value="ingest">Ingest</MenuItem>
              </Select>
            </FormControl>
            <Tooltip title={enabled ? 'Disable tool' : 'Enable tool'}>
              <Switch
                checked={enabled}
                onChange={(e) => handleEnabledChange(e.target.checked)}
                size="small"
                aria-label={`Toggle ${toolName}`}
              />
            </Tooltip>
            <Tooltip title="Save tool configuration">
              <span>
                <IconButton
                  onClick={handleSave}
                  disabled={!dirty || saveMut.isLoading}
                  size="small"
                  color={dirty ? 'primary' : 'default'}
                  aria-label={`Save ${toolName} configuration`}
                >
                  <SaveIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        </Stack>

        {type === 'ingest' && (
          <Box sx={{ mt: 1.5 }}>
            <McpIngestConfigForm config={ingestConfig} onChange={handleIngestChange} />
          </Box>
        )}

        {saveMut.isError && (
          <Alert severity="error" sx={{ mt: 1 }}>
            {formatError(saveMut.error)}
          </Alert>
        )}
      </CardContent>
    </Card>
  );
};

export default function McpToolConfigPanel({ connection }) {
  const [expanded, setExpanded] = useState(false);

  const discoveredTools = connection.discovered_tools || [];
  const toolConfigs = connection.tool_configs || {};

  if (discoveredTools.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
        No tools discovered. Click Sync to discover tools from the server.
      </Typography>
    );
  }

  return (
    <Box>
      <Button
        onClick={() => setExpanded(!expanded)}
        endIcon={expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        size="small"
        sx={{ mb: 1 }}
        aria-label={expanded ? 'Collapse tool list' : 'Expand tool list'}
      >
        {discoveredTools.length} tool{discoveredTools.length !== 1 ? 's' : ''}
      </Button>
      <Collapse in={expanded}>
        <Stack spacing={0}>
          {discoveredTools.map((tool) => (
            <ToolRow
              key={tool.name}
              connectionId={connection.id}
              toolName={tool.name}
              description={tool.description}
              config={toolConfigs[tool.name]}
            />
          ))}
        </Stack>
      </Collapse>
    </Box>
  );
}
