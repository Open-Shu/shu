import React, { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Stack,
  Typography,
  Checkbox,
  FormControlLabel,
  Box,
} from '@mui/material';
import { pluginsAPI } from '../services/pluginsApi';
import { extractDataFromResponse, formatError } from '../services/api';

export default function PluginUploadDialog({ open, onClose, onUploaded }) {
  const [file, setFile] = useState(null);
  const [force, setForce] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const handleFileChange = (e) => {
    setFile(e.target.files?.[0] || null);
    setError(null);
    setResult(null);
  };

  const handleUpload = async () => {
    if (!file) {
      setError('Select a .zip or .tgz plugin package');
      return;
    }
    setIsUploading(true);
    setError(null);
    setResult(null);
    try {
      const resp = await pluginsAPI.upload(file, force);
      const data = extractDataFromResponse(resp);
      setResult(data);
      if (onUploaded) onUploaded(data);
    } catch (err) {
      setError(formatError(err));
    } finally {
      setIsUploading(false);
    }
  };

  const handleClose = () => {
    setFile(null);
    setForce(false);
    setError(null);
    setResult(null);
    onClose?.();
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Upload Plugin Package</DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          <Typography variant="body2" color="text.secondary">
            Select a plugin package (.zip or .tar.gz) containing a single top-level folder with manifest.py and plugin code.
          </Typography>
          <input type="file" accept=".zip,.tar,.tgz,.tar.gz" onChange={handleFileChange} />
          <FormControlLabel
            control={<Checkbox checked={force} onChange={(e) => setForce(e.target.checked)} />}
            label="Overwrite if plugin already exists"
          />
          {error && (
            <Box sx={{ bgcolor: 'error.50', border: '1px solid', borderColor: 'error.200', borderRadius: 1, p: 1 }}>
              <Typography variant="body2" color="error.main">{error}</Typography>
            </Box>
          )}
          {result && (
            <Box sx={{ bgcolor: 'success.50', border: '1px solid', borderColor: 'success.200', borderRadius: 1, p: 1 }}>
              <Typography variant="body2" color="success.main">
                Installed {result.plugin_name} {result.version ? `(v${result.version})` : ''} at {result.installed_path}
              </Typography>
              {Array.isArray(result.warnings) && result.warnings.length > 0 && (
                <Typography variant="body2" color="warning.main">Warnings: {result.warnings.join('; ')}</Typography>
              )}
              {result.restart_required && (
                <Typography variant="body2" color="text.secondary">A server restart may be required to load the plugin.</Typography>
              )}
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={isUploading}>Close</Button>
        <Button onClick={handleUpload} variant="contained" disabled={isUploading}>
          {isUploading ? 'Uploading…' : 'Upload'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

