import React from 'react';
import { Alert, Box, Button, Chip, Table, TableBody, TableCell, TableRow, Typography } from '@mui/material';
import { CheckCircle as CheckIcon, Warning as WarningIcon } from '@mui/icons-material';

/**
 * ManifestPreviewStep - Second step of the Import KB wizard.
 *
 * Displays manifest data from a validated archive and warns about
 * embedding model mismatches.
 *
 * @param {Object} props.manifest - Validated manifest data from the backend
 * @param {boolean} props.skipEmbeddings - Current skip embeddings state
 * @param {function} props.onSkipEmbeddingsChange - Called when user toggles skip embeddings
 */
const ManifestPreviewStep = ({ manifest, skipEmbeddings, onSkipEmbeddingsChange }) => {
  if (!manifest) {
    return null;
  }

  const modelMatch = manifest.embedding_model_match;

  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 2 }}>
        Archive Contents
      </Typography>

      <Table size="small" sx={{ mb: 3 }}>
        <TableBody>
          <TableRow>
            <TableCell sx={{ fontWeight: 500 }}>Name</TableCell>
            <TableCell>{manifest.name}</TableCell>
          </TableRow>
          {manifest.description && (
            <TableRow>
              <TableCell sx={{ fontWeight: 500 }}>Description</TableCell>
              <TableCell>{manifest.description}</TableCell>
            </TableRow>
          )}
          <TableRow>
            <TableCell sx={{ fontWeight: 500 }}>Documents</TableCell>
            <TableCell>{manifest.document_count?.toLocaleString()}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell sx={{ fontWeight: 500 }}>Chunks</TableCell>
            <TableCell>{manifest.chunk_count?.toLocaleString()}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell sx={{ fontWeight: 500 }}>Queries</TableCell>
            <TableCell>{manifest.query_count?.toLocaleString()}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell sx={{ fontWeight: 500 }}>Embedding Model</TableCell>
            <TableCell>
              <Chip label={manifest.embedding_model} size="small" variant="outlined" />
            </TableCell>
          </TableRow>
          <TableRow>
            <TableCell sx={{ fontWeight: 500 }}>Schema Version</TableCell>
            <TableCell>{manifest.schema_version}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell sx={{ fontWeight: 500 }}>Exported</TableCell>
            <TableCell>{new Date(manifest.export_timestamp).toLocaleString()}</TableCell>
          </TableRow>
        </TableBody>
      </Table>

      {modelMatch ? (
        <Alert icon={<CheckIcon />} severity="success">
          Embedding model matches this instance ({manifest.instance_embedding_model}). All embeddings will be preserved.
        </Alert>
      ) : (
        <Alert icon={<WarningIcon />} severity="warning" sx={{ mb: 2 }}>
          <Typography variant="body2" sx={{ mb: 1 }}>
            <strong>Embedding model mismatch.</strong> The archive uses{' '}
            <Chip label={manifest.embedding_model} size="small" variant="outlined" sx={{ mx: 0.5 }} /> but this instance
            uses <Chip label={manifest.instance_embedding_model} size="small" variant="outlined" sx={{ mx: 0.5 }} />.
          </Typography>
          <Typography variant="body2" sx={{ mb: 1 }}>
            You can import without embeddings and re-embed later, or cancel and re-export from a compatible instance.
          </Typography>
          {!skipEmbeddings ? (
            <Button size="small" variant="outlined" color="warning" onClick={() => onSkipEmbeddingsChange(true)}>
              Import without embeddings
            </Button>
          ) : (
            <Alert severity="info" sx={{ mt: 1 }}>
              Embeddings will be skipped. You can re-embed from the knowledge base settings after import.
            </Alert>
          )}
        </Alert>
      )}
    </Box>
  );
};

export default ManifestPreviewStep;
