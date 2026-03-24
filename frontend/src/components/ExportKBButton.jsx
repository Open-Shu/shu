import React, { useState } from 'react';
import { Button, CircularProgress, IconButton, Tooltip } from '@mui/material';
import { Download as DownloadIcon } from '@mui/icons-material';
import { useMutation } from 'react-query';
import { knowledgeBaseAPI, formatError } from '../services/api';
import { downloadResponseAsFile, generateSafeFilename } from '../utils/downloadHelpers';
import { log } from '../utils/log';

/**
 * ExportKBButton component for exporting knowledge bases as zip archives.
 *
 * @param {Object} props - Component props
 * @param {string} props.kbId - ID of the knowledge base to export
 * @param {string} props.kbName - Name of the knowledge base (for filename)
 * @param {string} [props.variant='icon'] - Button variant: 'icon', 'button', or 'contained'
 * @param {string} [props.size='small'] - Button size
 * @param {boolean} [props.disabled=false] - Whether the button is disabled
 * @param {function} [props.onSuccess] - Callback function called on successful export
 * @param {function} [props.onError] - Callback function called on export error
 */
const ExportKBButton = ({ kbId, kbName, variant = 'icon', size = 'small', disabled = false, onSuccess, onError }) => {
  const [error, setError] = useState(null);

  const exportMutation = useMutation(() => knowledgeBaseAPI.exportKB(kbId), {
    onSuccess: (response) => {
      try {
        const safeName = generateSafeFilename(kbName, 'knowledge-base');
        const filename = `${safeName}-export.zip`;

        downloadResponseAsFile(response, filename, 'application/zip');

        setError(null);

        log.info('Knowledge base exported successfully', {
          kbId,
          kbName,
          filename,
        });

        if (onSuccess) {
          onSuccess();
        }
      } catch (downloadError) {
        log.error('Failed to trigger download', downloadError);
        const errorMessage = 'Failed to download the exported file';
        setError(errorMessage);
        if (onError) {
          onError(errorMessage);
        }
      }
    },
    onError: (error) => {
      const errorMessage = formatError(error);
      log.error('Failed to export knowledge base', {
        error: errorMessage,
        kbId,
      });
      setError(errorMessage);
      if (onError) {
        onError(errorMessage);
      }
    },
  });

  const handleExport = () => {
    if (!kbId) {
      const errorMessage = 'Knowledge base ID is required for export';
      setError(errorMessage);
      if (onError) {
        onError(errorMessage);
      }
      return;
    }

    exportMutation.mutate();
  };

  if (variant === 'icon') {
    return (
      <Tooltip title={error || 'Export knowledge base'}>
        <span>
          <IconButton
            size={size}
            onClick={handleExport}
            disabled={disabled || exportMutation.isLoading}
            color={error ? 'error' : 'default'}
            aria-label={error || 'Export knowledge base'}
          >
            {exportMutation.isLoading ? <CircularProgress size={16} /> : <DownloadIcon fontSize="small" />}
          </IconButton>
        </span>
      </Tooltip>
    );
  }

  const buttonProps = {
    size,
    onClick: handleExport,
    disabled: disabled || exportMutation.isLoading,
    startIcon: exportMutation.isLoading ? <CircularProgress size={16} /> : <DownloadIcon />,
    color: error ? 'error' : 'primary',
  };

  if (variant === 'contained') {
    buttonProps.variant = 'contained';
  } else {
    buttonProps.variant = 'outlined';
  }

  return (
    <Tooltip title={error || 'Export knowledge base'}>
      <span>
        <Button {...buttonProps}>{exportMutation.isLoading ? 'Exporting...' : 'Export'}</Button>
      </span>
    </Tooltip>
  );
};

export default ExportKBButton;
