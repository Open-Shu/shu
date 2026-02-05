import React, { useEffect, useMemo, useState } from 'react';
import {
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  FormGroup,
  Typography,
  Box,
} from '@mui/material';

const EnsembleModeDialog = ({ open, onClose, onSave, availableModelConfigs, selectedIds, currentModelConfigId }) => {
  const safeSelectedIds = useMemo(() => (Array.isArray(selectedIds) ? selectedIds : []), [selectedIds]);
  const [pendingSelection, setPendingSelection] = useState(() => new Set(safeSelectedIds));

  useEffect(() => {
    setPendingSelection(new Set(safeSelectedIds));
  }, [safeSelectedIds, open]);

  const handleToggle = (id) => {
    // Don't allow toggling the current model - it's always included implicitly
    if (id === currentModelConfigId) {
      return;
    }
    setPendingSelection((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleApply = () => {
    // Filter out the current model ID before saving - backend includes it automatically
    const idsToSave = Array.from(pendingSelection).filter((id) => id !== currentModelConfigId);
    onSave(idsToSave);
  };

  const handleClearAll = () => {
    setPendingSelection(new Set());
  };

  // Count only additional models (not the current one)
  const additionalSelectionCount = Array.from(pendingSelection).filter((id) => id !== currentModelConfigId).length;
  const hasSelection = additionalSelectionCount > 0;

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Configure Ensemble Mode</DialogTitle>
      <DialogContent dividers>
        {availableModelConfigs.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No additional model configurations are available.
          </Typography>
        ) : (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <Typography variant="body2" color="text.secondary">
              Select one or more model configurations to run alongside the conversation&apos;s primary model.
            </Typography>
            <FormGroup>
              {availableModelConfigs.map((config) => {
                const isCurrentModel = config.id === currentModelConfigId;
                return (
                  <FormControlLabel
                    key={config.id}
                    disabled={isCurrentModel}
                    control={
                      <Checkbox
                        checked={isCurrentModel || pendingSelection.has(config.id)}
                        onChange={() => handleToggle(config.id)}
                        disabled={isCurrentModel}
                      />
                    }
                    label={
                      <Box sx={{ display: 'flex', flexDirection: 'column' }}>
                        <Typography variant="body2" sx={isCurrentModel ? { fontStyle: 'italic' } : undefined}>
                          {config.name}
                          {isCurrentModel && ' (current)'}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {config.llm_provider?.name || 'Provider'} • {config.model_name}
                        </Typography>
                      </Box>
                    }
                  />
                );
              })}
            </FormGroup>
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClearAll} disabled={!hasSelection}>
          Clear
        </Button>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={handleApply} variant="contained">
          Apply
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default EnsembleModeDialog;
