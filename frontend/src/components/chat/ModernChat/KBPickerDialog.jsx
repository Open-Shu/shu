import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  FormGroup,
  TextField,
  Typography,
} from '@mui/material';
import { useQuery } from 'react-query';
import { knowledgeBaseAPI, extractItemsFromResponse, formatError } from '../../../services/api';

export default function KBPickerDialog({ open, onClose, onSave, selectedKBs }) {
  const safeSelected = useMemo(() => (Array.isArray(selectedKBs) ? selectedKBs : []), [selectedKBs]);
  const [pendingSelection, setPendingSelection] = useState(() => new Set(safeSelected.map((kb) => kb.id)));
  const [filter, setFilter] = useState('');

  useEffect(() => {
    setPendingSelection(new Set(safeSelected.map((kb) => kb.id)));
    setFilter('');
  }, [safeSelected, open]);

  const { data, isLoading, isFetching, error } = useQuery(
    ['knowledge-bases-for-chat'],
    () => knowledgeBaseAPI.list().then(extractItemsFromResponse),
    { enabled: open, staleTime: 10000 }
  );

  const kbList = useMemo(() => (Array.isArray(data) ? data : []), [data]);

  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) {
      return kbList;
    }
    return kbList.filter((kb) => (kb.name || '').toLowerCase().includes(f));
  }, [kbList, filter]);

  const handleToggle = (kbId) => {
    setPendingSelection((prev) => {
      const next = new Set(prev);
      if (next.has(kbId)) {
        next.delete(kbId);
      } else {
        next.add(kbId);
      }
      return next;
    });
  };

  const handleApply = () => {
    const selected = kbList.filter((kb) => pendingSelection.has(kb.id)).map((kb) => ({ id: kb.id, name: kb.name }));
    onSave(selected);
  };

  const handleClearAll = () => {
    setPendingSelection(new Set());
  };

  const hasSelection = pendingSelection.size > 0;

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Attach Knowledge Base</DialogTitle>
      <DialogContent dividers>
        {isLoading || isFetching ? (
          <CircularProgress size={20} />
        ) : error ? (
          <Typography color="error">{formatError(error)}</Typography>
        ) : kbList.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No knowledge bases available.
          </Typography>
        ) : (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <TextField
              size="small"
              placeholder="Filter by name..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              fullWidth
              autoFocus
            />
            <FormGroup>
              {filtered.map((kb) => (
                <FormControlLabel
                  key={kb.id}
                  control={<Checkbox checked={pendingSelection.has(kb.id)} onChange={() => handleToggle(kb.id)} />}
                  label={
                    <Box sx={{ display: 'flex', flexDirection: 'column' }}>
                      <Typography variant="body2">{kb.name}</Typography>
                      {kb.description && (
                        <Typography variant="caption" color="text.secondary">
                          {kb.description}
                        </Typography>
                      )}
                    </Box>
                  }
                />
              ))}
              {filtered.length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No knowledge bases match your filter.
                </Typography>
              )}
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
}
