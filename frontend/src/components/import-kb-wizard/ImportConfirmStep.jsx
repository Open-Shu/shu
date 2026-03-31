import React, { useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, CircularProgress, Typography } from '@mui/material';
import { CheckCircle as CheckIcon } from '@mui/icons-material';
import { knowledgeBaseAPI, extractDataFromResponse, formatError } from '../../services/api';
import { log } from '../../utils/log';

/**
 * ImportConfirmStep - Final step of the Import KB wizard.
 *
 * Calls the import endpoint on mount, shows progress, and reports
 * the result to the parent.
 *
 * @param {File} props.file - The zip archive file to import
 * @param {boolean} props.skipEmbeddings - Whether to skip embeddings
 * @param {function} props.onSuccess - Called with import result on success
 * @param {function} props.onRetry - Called when user wants to retry (go back)
 */
const ImportConfirmStep = ({ file, skipEmbeddings, onSuccess, onRetry }) => {
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const importStarted = useRef(false);

  useEffect(() => {
    if (!file || importStarted.current) {
      return;
    }
    importStarted.current = true;

    const doImport = async () => {
      setImporting(true);
      setError(null);

      try {
        const response = await knowledgeBaseAPI.importKB(file, skipEmbeddings);
        const data = extractDataFromResponse(response);
        setResult(data);

        log.info('KB import queued', { kb_id: data.knowledge_base_id, name: data.name });

        if (onSuccess) {
          onSuccess(data);
        }
      } catch (err) {
        const message = formatError(err);
        log.error('KB import failed', { error: message });
        setError(message);
      } finally {
        setImporting(false);
      }
    };

    doImport();
  }, [file, skipEmbeddings]); // eslint-disable-line react-hooks/exhaustive-deps

  if (importing) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', py: 4, gap: 2 }}>
        <CircularProgress />
        <Typography variant="body1">Uploading and starting import...</Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Box>
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
        {onRetry && (
          <Button variant="outlined" onClick={onRetry}>
            Go Back
          </Button>
        )}
      </Box>
    );
  }

  if (result) {
    return (
      <Alert icon={<CheckIcon />} severity="success">
        <Typography variant="body2">
          <strong>{result.name}</strong> import has been queued. The knowledge base will appear in your list shortly
          with status &quot;importing&quot;.
        </Typography>
      </Alert>
    );
  }

  return null;
};

export default ImportConfirmStep;
