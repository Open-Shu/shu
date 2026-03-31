import React, { useState } from 'react';
import { Alert, Box, CircularProgress, Typography } from '@mui/material';
import FileDropzone from '../shared/FileDropzone';
import { knowledgeBaseAPI, extractDataFromResponse, formatError } from '../../services/api';
import { log } from '../../utils/log';

/**
 * FileUploadStep - First step of the Import KB wizard.
 *
 * Accepts a .zip file, validates it against the backend, and passes
 * the manifest data to the parent via onManifestLoaded.
 *
 * @param {function} props.onManifestLoaded - Called with manifest data on successful validation
 * @param {function} props.onFileSelected - Called with the selected File object
 */
const FileUploadStep = ({ onManifestLoaded, onFileSelected }) => {
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState(null);

  const handleFilesSelected = async (files) => {
    const file = files[0];
    if (!file) {
      return;
    }

    if (onFileSelected) {
      onFileSelected(file);
    }

    setValidating(true);
    setError(null);

    try {
      const response = await knowledgeBaseAPI.validateImport(file);
      const manifest = extractDataFromResponse(response);

      log.info('Import archive validated', { name: manifest.name });

      if (onManifestLoaded) {
        onManifestLoaded(manifest);
      }
    } catch (err) {
      const message = formatError(err);
      log.error('Import validation failed', { error: message });
      setError(message);
    } finally {
      setValidating(false);
    }
  };

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Select a knowledge base archive (.zip) exported from Shu.
      </Typography>

      <Alert severity="warning" sx={{ mb: 2 }}>
        Only import archives from sources you trust. Malicious archives could contain harmful data.
      </Alert>

      <FileDropzone
        allowedTypes={['zip']}
        multiple={false}
        disabled={validating}
        maxSizeBytes={500 * 1024 * 1024}
        onFilesSelected={handleFilesSelected}
      />

      {validating && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 2 }}>
          <CircularProgress size={20} />
          <Typography variant="body2" color="text.secondary">
            Validating archive...
          </Typography>
        </Box>
      )}

      {error && (
        <Alert severity="error" sx={{ mt: 2 }}>
          {error}
        </Alert>
      )}
    </Box>
  );
};

export default FileUploadStep;
