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

const EnsembleModeDialog = ({
  open,
  onClose,
  onSave,
  availableModelConfigs,
  selectedIds,
}) => {
  const safeSelectedIds = useMemo(() => Array.isArray(selectedIds) ? selectedIds : [], [selectedIds]);
  const [pendingSelection, setPendingSelection] = useState(() => new Set(safeSelectedIds));

  useEffect(() => {
    setPendingSelection(new Set(safeSelectedIds));
  }, [safeSelectedIds, open]);

  const handleToggle = (id) => {
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
    onSave(Array.from(pendingSelection));
  };

  const handleClearAll = () => {
    setPendingSelection(new Set());
  };

  const hasSelection = pendingSelection.size > 0;

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
              {availableModelConfigs.map((config) => (
                <FormControlLabel
                  key={config.id}
                  control={
                    <Checkbox
                      checked={pendingSelection.has(config.id)}
                      onChange={() => handleToggle(config.id)}
                    />
                  }
                  label={
                    <Box sx={{ display: 'flex', flexDirection: 'column' }}>
                      <Typography variant="body2">{config.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {(config.llm_provider?.name || 'Provider')} • {config.model_name}
                      </Typography>
                    </Box>
                  }
                />
              ))}
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
