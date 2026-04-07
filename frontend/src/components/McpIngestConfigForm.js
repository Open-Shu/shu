import React, { useState } from 'react';
import {
  Alert,
  Box,
  Button,
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
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';

const REQUIRED_FIELDS = ['title', 'content', 'source_id'];

const AttributeRow = ({ attrKey, attrValue, index, onChange, onRemove }) => (
  <Stack direction="row" spacing={1} alignItems="center">
    <TextField
      label="Key"
      value={attrKey}
      onChange={(e) => onChange(index, e.target.value, attrValue)}
      size="small"
      sx={{ flex: 1 }}
      placeholder="source_type"
    />
    <TextField
      label="Value"
      value={attrValue}
      onChange={(e) => onChange(index, attrKey, e.target.value)}
      size="small"
      sx={{ flex: 1 }}
      placeholder="confluence"
    />
    <Tooltip title="Remove attribute">
      <IconButton onClick={() => onRemove(index)} size="small" aria-label="Remove attribute">
        <DeleteIcon fontSize="small" />
      </IconButton>
    </Tooltip>
  </Stack>
);

export default function McpIngestConfigForm({ config, onChange }) {
  const cfg = config || {};
  const fieldMapping = cfg.field_mapping || {};
  const attributes = cfg.attributes || {};
  const [attrEntries, setAttrEntries] = useState(Object.entries(attributes).map(([k, v]) => ({ key: k, value: v })));

  const update = (patch) => {
    onChange({ ...cfg, ...patch });
  };

  const updateFieldMapping = (field, value) => {
    update({ field_mapping: { ...fieldMapping, [field]: value } });
  };

  const handleAttrChange = (index, key, value) => {
    const updated = attrEntries.map((e, i) => (i === index ? { key, value } : e));
    setAttrEntries(updated);
    const attrs = {};
    updated.forEach((e) => {
      if (e.key.trim()) {
        attrs[e.key.trim()] = e.value;
      }
    });
    update({ attributes: Object.keys(attrs).length > 0 ? attrs : undefined });
  };

  const handleAttrRemove = (index) => {
    const updated = attrEntries.filter((_, i) => i !== index);
    setAttrEntries(updated);
    const attrs = {};
    updated.forEach((e) => {
      if (e.key.trim()) {
        attrs[e.key.trim()] = e.value;
      }
    });
    update({ attributes: Object.keys(attrs).length > 0 ? attrs : undefined });
  };

  const handleAttrAdd = () => {
    setAttrEntries((prev) => [...prev, { key: '', value: '' }]);
  };

  const missingRequired = REQUIRED_FIELDS.filter((f) => !fieldMapping[f]?.trim());

  return (
    <Box sx={{ p: 1.5, bgcolor: 'grey.50', borderRadius: 1 }}>
      <Stack spacing={2}>
        <FormControl size="small" sx={{ maxWidth: 200 }}>
          <InputLabel>Method</InputLabel>
          <Select value={cfg.method || 'text'} label="Method" onChange={(e) => update({ method: e.target.value })}>
            <MenuItem value="text">Text</MenuItem>
            <MenuItem value="document">Document</MenuItem>
          </Select>
        </FormControl>

        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Field Mapping
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
            Dot-notation paths to extract fields from the MCP tool response.
          </Typography>
          <Stack spacing={1}>
            <Stack direction="row" spacing={1}>
              <TextField
                label="title *"
                value={fieldMapping.title || ''}
                onChange={(e) => updateFieldMapping('title', e.target.value)}
                size="small"
                placeholder="title"
                sx={{ flex: 1 }}
              />
              <TextField
                label="content *"
                value={fieldMapping.content || ''}
                onChange={(e) => updateFieldMapping('content', e.target.value)}
                size="small"
                placeholder="body"
                sx={{ flex: 1 }}
              />
            </Stack>
            <Stack direction="row" spacing={1}>
              <TextField
                label="source_id *"
                value={fieldMapping.source_id || ''}
                onChange={(e) => updateFieldMapping('source_id', e.target.value)}
                size="small"
                placeholder="id"
                sx={{ flex: 1 }}
              />
              <TextField
                label="source_url"
                value={fieldMapping.source_url || ''}
                onChange={(e) => updateFieldMapping('source_url', e.target.value)}
                size="small"
                placeholder="url"
                sx={{ flex: 1 }}
              />
            </Stack>
          </Stack>
          {missingRequired.length > 0 && (
            <Alert severity="warning" sx={{ mt: 1 }}>
              Required fields unmapped: {missingRequired.join(', ')}
            </Alert>
          )}
        </Box>

        <TextField
          label="Collection Field"
          value={cfg.collection_field || ''}
          onChange={(e) => update({ collection_field: e.target.value || undefined })}
          size="small"
          placeholder="pages"
          helperText="Dot-notation path to the array of items in the response. Leave empty for single-item responses."
        />

        <Stack direction="row" spacing={1}>
          <TextField
            label="Cursor Field"
            value={cfg.cursor_field || ''}
            onChange={(e) => update({ cursor_field: e.target.value || undefined })}
            size="small"
            placeholder="next_cursor"
            sx={{ flex: 1 }}
            helperText="Response path for next-page cursor"
          />
          <TextField
            label="Cursor Param"
            value={cfg.cursor_param || ''}
            onChange={(e) => update({ cursor_param: e.target.value || undefined })}
            size="small"
            placeholder="cursor"
            sx={{ flex: 1 }}
            helperText="Tool argument name for cursor"
          />
        </Stack>

        <Box>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="subtitle2">Static Attributes</Typography>
            <Button size="small" startIcon={<AddIcon />} onClick={handleAttrAdd} aria-label="Add static attribute">
              Add
            </Button>
          </Stack>
          {attrEntries.length === 0 && (
            <Typography variant="caption" color="text.secondary">
              No static attributes. These are attached to every ingested item.
            </Typography>
          )}
          <Stack spacing={1}>
            {attrEntries.map((entry, i) => (
              <AttributeRow
                key={i}
                attrKey={entry.key}
                attrValue={entry.value}
                index={i}
                onChange={handleAttrChange}
                onRemove={handleAttrRemove}
              />
            ))}
          </Stack>
        </Box>
      </Stack>
    </Box>
  );
}
