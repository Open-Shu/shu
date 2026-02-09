import React from 'react';
import { Box, FormControl, InputLabel, MenuItem, Select, Typography } from '@mui/material';

/**
 * Reusable model configuration selector component.
 * Used in ChatHeader (desktop) and ChatSettingsDialog (mobile).
 */
const ModelConfigSelector = React.memo(function ModelConfigSelector({
  availableModelConfigs = [],
  selectedModelConfig,
  onModelChange,
  disabled = false,
  size = 'small',
  minWidth = 220,
  fullWidth = false,
}) {
  if (!Array.isArray(availableModelConfigs) || availableModelConfigs.length === 0) {
    return null;
  }

  return (
    <FormControl size={size} sx={{ minWidth, ...(fullWidth && { width: '100%' }) }}>
      <InputLabel>Model</InputLabel>
      <Select value={selectedModelConfig || ''} label="Model" onChange={onModelChange} disabled={disabled} displayEmpty>
        <MenuItem value="" disabled>
          <Typography variant="body2" color="text.secondary">
            Select a model
          </Typography>
        </MenuItem>
        {availableModelConfigs.map((config) => (
          <MenuItem key={config.id} value={config.id}>
            <Box>
              <Typography variant="body2" noWrap>
                {config.name}
              </Typography>
              <Typography variant="caption" color="text.secondary" noWrap>
                {config.llm_provider?.name || 'Provider'} • {config.model_name}
              </Typography>
            </Box>
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
});

export default ModelConfigSelector;
