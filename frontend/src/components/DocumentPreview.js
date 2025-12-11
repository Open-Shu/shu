import React, { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  CircularProgress,
  Alert
} from '@mui/material';
import { Description } from '@mui/icons-material';
import DocumentPreviewContent from './shared/DocumentPreviewContent';
import api, { extractDataFromResponse } from '../services/api';

const DocumentPreview = ({
  open,
  onClose,
  kbId,
  documentId,
  maxChars = 1000,
  showExtractionDetails = true,
}) => {
  const [document, setDocument] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showFullContent, setShowFullContent] = useState(false);
  const [loadingFullContent, setLoadingFullContent] = useState(false);

  const fetchDocumentPreview = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.get(`/knowledge-bases/${kbId}/documents/${documentId}/preview`, {
        params: { max_chars: maxChars },
      });
      const data = extractDataFromResponse(response);
      setDocument(data);
    } catch (err) {
      setError(err.message || 'Failed to load document preview');
    } finally {
      setLoading(false);
    }
  }, [kbId, documentId, maxChars]);

  // Load preview whenever dialog opens with valid ids
  React.useEffect(() => {
    if (open && kbId && documentId) {
      setShowFullContent(false);
      fetchDocumentPreview();
    }
  }, [open, kbId, documentId, fetchDocumentPreview]);

  const fetchFullContent = async () => {
    setLoadingFullContent(true);
    setError(null);

    try {
      const response = await api.get(`/knowledge-bases/${kbId}/documents/${documentId}/preview`, {
        params: { max_chars: 0 },
      });
      const data = extractDataFromResponse(response);
      setDocument(data);
      setShowFullContent(true);
    } catch (err) {
      setError(err.message || 'Failed to load full document');
    } finally {
      setLoadingFullContent(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: { maxHeight: '90vh' }
      }}
    >
      <DialogTitle>
        <Box display="flex" alignItems="center" gap={1}>
          <Description />
          <Typography variant="h6">
            Document Preview: {document?.title || 'Loading...'}
          </Typography>
        </Box>
      </DialogTitle>

      <DialogContent>
        {loading && (
          <Box display="flex" justifyContent="center" p={3}>
            <CircularProgress />
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {document && (
          <DocumentPreviewContent
            document={document}
            maxChars={maxChars}
            showFullContent={showFullContent}
            loadingFullContent={loadingFullContent}
            onShowFullContent={fetchFullContent}
            showExtractionDetails={showExtractionDetails}
          />
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default DocumentPreview;
