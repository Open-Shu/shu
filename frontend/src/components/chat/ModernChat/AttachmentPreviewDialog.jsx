import React, { useState, useEffect, useRef } from 'react';
import { Dialog, DialogTitle, DialogContent, IconButton, Box, Typography, CircularProgress } from '@mui/material';
import { Close as CloseIcon, Download as DownloadIcon, OpenInNew as OpenInNewIcon } from '@mui/icons-material';
import { chatAPI } from '../../../services/api';

/**
 * AttachmentPreviewDialog - Shows attachment content in a modal dialog.
 * Supports images (inline), PDFs (iframe), and text files (pre-formatted).
 */
const AttachmentPreviewDialog = ({ open, onClose, attachment }) => {
  const [content, setContent] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [blobUrl, setBlobUrl] = useState(null);
  const blobUrlRef = useRef(null);

  // Keep ref in sync with state for cleanup
  useEffect(() => {
    blobUrlRef.current = blobUrl;
  }, [blobUrl]);

  useEffect(() => {
    if (!open || !attachment) {
      setContent(null);
      setError(null);
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        setBlobUrl(null);
      }
      return;
    }

    const fetchAttachment = async () => {
      setLoading(true);
      setError(null);
      try {
        const response = await chatAPI.viewAttachment(attachment.id);
        const blob = response.data;
        const url = URL.createObjectURL(blob);
        setBlobUrl(url);
        setContent({ blob, url });
      } catch (err) {
        console.error('Error fetching attachment:', err);
        setError(err.response?.status ? `Failed to fetch attachment: ${err.response.status}` : err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchAttachment();

    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
      }
    };
  }, [open, attachment?.id]);

  const handleDownload = () => {
    if (blobUrl && attachment) {
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = attachment.original_filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }
  };

  const handleOpenInNewTab = () => {
    if (blobUrl) {
      window.open(blobUrl, '_blank');
    }
  };

  const renderContent = () => {
    if (loading) {
      return (
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: 300,
          }}
        >
          <CircularProgress />
        </Box>
      );
    }

    if (error) {
      return (
        <Box sx={{ textAlign: 'center', py: 4 }}>
          <Typography color="error">{error}</Typography>
        </Box>
      );
    }

    if (!content || !attachment) {
      return null;
    }

    const mimeType = attachment.mime_type || '';

    // Images
    if (mimeType.startsWith('image/')) {
      return (
        <Box sx={{ textAlign: 'center' }}>
          <img
            src={content.url}
            alt={attachment.original_filename}
            style={{
              maxWidth: '100%',
              maxHeight: '70vh',
              objectFit: 'contain',
            }}
          />
        </Box>
      );
    }

    // PDFs
    if (mimeType === 'application/pdf') {
      return (
        <Box sx={{ height: '70vh' }}>
          <iframe
            src={content.url}
            title={attachment.original_filename}
            style={{ width: '100%', height: '100%', border: 'none' }}
          />
        </Box>
      );
    }

    // Text files
    if (mimeType.startsWith('text/') || ['application/json', 'application/xml'].includes(mimeType)) {
      return (
        <Box sx={{ maxHeight: '70vh', overflow: 'auto' }}>
          <TextFileViewer blob={content.blob} />
        </Box>
      );
    }

    // Unsupported - offer download
    return (
      <Box sx={{ textAlign: 'center', py: 4 }}>
        <Typography sx={{ mb: 2 }}>Preview not available for this file type ({mimeType})</Typography>
        <Typography variant="body2" color="text.secondary">
          Click the download button to save the file.
        </Typography>
      </Box>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      PaperProps={{
        sx: { minHeight: '50vh' },
      }}
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 6 }}>
        <Typography variant="h6" component="span" sx={{ flexGrow: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {attachment?.original_filename}
        </Typography>
        <IconButton size="small" onClick={handleDownload} title="Download" disabled={!blobUrl}>
          <DownloadIcon fontSize="small" />
        </IconButton>
        <IconButton size="small" onClick={handleOpenInNewTab} title="Open in new tab" disabled={!blobUrl}>
          <OpenInNewIcon fontSize="small" />
        </IconButton>
        <IconButton aria-label="close" onClick={onClose} sx={{ position: 'absolute', right: 8, top: 8 }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>{renderContent()}</DialogContent>
    </Dialog>
  );
};

// Helper component to read and display text files
const TextFileViewer = ({ blob }) => {
  const [text, setText] = useState('');

  useEffect(() => {
    const reader = new FileReader();
    reader.onload = (e) => setText(e.target.result);
    reader.readAsText(blob);
  }, [blob]);

  return (
    <pre
      style={{
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        margin: 0,
        fontFamily: 'monospace',
        fontSize: '0.9rem',
      }}
    >
      {text}
    </pre>
  );
};

export default AttachmentPreviewDialog;
