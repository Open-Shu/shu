import React from 'react';
import { Box, Typography, Button, Dialog, DialogTitle, DialogContent, DialogActions, Alert } from '@mui/material';
import { formatError } from '../services/api';

function ReEmbedConfirmDialog({ knowledgeBase, onClose, onConfirm, isLoading, error }) {
  return (
    <Dialog open={Boolean(knowledgeBase)} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Re-embed Knowledge Base</DialogTitle>
      <DialogContent>
        {knowledgeBase && (
          <Box>
            <Alert severity="info" sx={{ mb: 2 }}>
              Re-embedding will regenerate all vector embeddings for <strong>{knowledgeBase.name}</strong> using the
              current embedding model. This process runs in the background.
            </Alert>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              <strong>Documents:</strong> {knowledgeBase.document_count || 0}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              <strong>Chunks:</strong> {knowledgeBase.total_chunks || 0}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Vector search will be restored for this knowledge base once re-embedding completes.
            </Typography>
            {error && (
              <Alert severity="error" sx={{ mt: 2 }}>
                Failed to start re-embedding: {formatError(error).message}
              </Alert>
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={onConfirm} variant="contained" color="warning" disabled={isLoading}>
          {isLoading ? 'Starting...' : 'Start Re-embedding'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default ReEmbedConfirmDialog;
