import React from 'react';
import {
  Box,
  Button,
  Grid,
  Paper,
  Typography,
  CircularProgress,
} from '@mui/material';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';

const formatNumber = (value) =>
  typeof value === 'number' ? value.toLocaleString() : 'N/A';

const formatConfidence = (value) =>
  typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : 'N/A';

const formatDuration = (value) =>
  typeof value === 'number' ? `${value.toFixed(2)}s` : 'N/A';

const DocumentPreviewContent = ({
  document,
  maxChars,
  showFullContent,
  loadingFullContent,
  onShowFullContent,
  showExtractionDetails = false,
}) => {
  if (!document) {
    return null;
  }

  const {
    knowledge_base_id: knowledgeBaseId,
    id,
    file_type: fileType,
    source_id: sourceId,
    source_url: sourceUrl,
    processing_info: processingInfo = {},
    preview = '',
    full_content_length: fullContentLength = 0,
    extraction_metadata: extractionMetadataRaw = null,
  } = document;

  const extraction = extractionMetadataRaw || null;
  const extractionMetadataDetails = extraction?.metadata;

  const shouldShowFullContentButton =
    fullContentLength > maxChars && !showFullContent && typeof onShowFullContent === 'function';

  const isInternalPreview =
    sourceUrl && knowledgeBaseId && id && sourceUrl === `/documents/${knowledgeBaseId}/${id}`;

  return (
    <Box>
      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="h6" gutterBottom>
          Document Information
        </Typography>
        <Grid container spacing={2}>
          <Grid item xs={6}>
            <Typography variant="body2" color="text.secondary">
              Knowledge Base
            </Typography>
            <Typography variant="body1">
              {knowledgeBaseId || 'Unknown'}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography variant="body2" color="text.secondary">
              Document ID
            </Typography>
            <Typography variant="body1">
              {id || 'Unknown'}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography variant="body2" color="text.secondary">
              File Type
            </Typography>
            <Typography variant="body1">
              {fileType ? fileType.toUpperCase() : 'Unknown'}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography variant="body2" color="text.secondary">
              Source ID
            </Typography>
            <Typography variant="body1">
              {sourceId || 'N/A'}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography variant="body2" color="text.secondary">
              Characters
            </Typography>
            <Typography variant="body1">
              {formatNumber(processingInfo.character_count)}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography variant="body2" color="text.secondary">
              Words
            </Typography>
            <Typography variant="body1">
              {formatNumber(processingInfo.word_count)}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography variant="body2" color="text.secondary">
              Chunks
            </Typography>
            <Typography variant="body1">
              {formatNumber(processingInfo.chunk_count)}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography variant="body2" color="text.secondary">
              Processed At
            </Typography>
            <Typography variant="body1">
              {processingInfo.processed_at || 'N/A'}
            </Typography>
          </Grid>
        </Grid>

        {sourceUrl && !isInternalPreview && (
          <Box sx={{ mt: 2 }}>
            <Button
              variant="outlined"
              size="small"
              endIcon={<OpenInNewIcon />}
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open Original Source
            </Button>
          </Box>
        )}
      </Paper>

      {showExtractionDetails && extraction && (
        <Paper sx={{ p: 2, mb: 2 }}>
          <Typography variant="h6" gutterBottom>
            Extraction Details
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={6}>
              <Typography variant="body2" color="text.secondary">
                Method
              </Typography>
              <Typography variant="body1">
                {extraction.method || 'Unknown'}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2" color="text.secondary">
                Engine
              </Typography>
              <Typography variant="body1">
                {extraction.engine || 'Unknown'}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2" color="text.secondary">
                Confidence
              </Typography>
              <Typography variant="body1">
                {formatConfidence(extraction.confidence)}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2" color="text.secondary">
                Duration
              </Typography>
              <Typography variant="body1">
                {formatDuration(extraction.duration)}
              </Typography>
            </Grid>
          </Grid>

          {extractionMetadataDetails && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Additional Details
              </Typography>
              <Paper variant="outlined" sx={{ p: 1 }}>
                <Typography
                  variant="body2"
                  component="pre"
                  sx={{
                    fontSize: '0.75rem',
                    whiteSpace: 'pre-wrap',
                    fontFamily: 'monospace',
                    mb: 0,
                  }}
                >
                  {JSON.stringify(extractionMetadataDetails, null, 2)}
                </Typography>
              </Paper>
            </Box>
          )}
        </Paper>
      )}

      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Content Preview
        </Typography>
        <Box
          sx={{
            maxHeight: 360,
            overflow: 'auto',
            border: 1,
            borderColor: 'divider',
            borderRadius: 1,
            p: 2,
            bgcolor: 'grey.50',
          }}
        >
          <Typography
            variant="body2"
            component="pre"
            sx={{
              whiteSpace: 'pre-wrap',
              fontFamily: 'inherit',
              margin: 0,
            }}
          >
            {preview || 'No extracted text available.'}
          </Typography>
        </Box>

        {shouldShowFullContentButton && (
          <Box sx={{ mt: 2, display: 'flex', alignItems: 'center', gap: 2 }}>
            <Typography variant="caption" color="text.secondary">
              Showing first {maxChars.toLocaleString()} characters of{' '}
              {fullContentLength.toLocaleString()} total
            </Typography>
            <Button
              variant="outlined"
              size="small"
              onClick={onShowFullContent}
              disabled={loadingFullContent}
              startIcon={
                loadingFullContent ? <CircularProgress size={16} /> : null
              }
            >
              {loadingFullContent ? 'Loading...' : 'Show All Content'}
            </Button>
          </Box>
        )}

        {showFullContent && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mt: 1, display: 'block' }}
          >
            Showing full content ({fullContentLength.toLocaleString()} characters)
          </Typography>
        )}
      </Paper>
    </Box>
  );
};

export default DocumentPreviewContent;
