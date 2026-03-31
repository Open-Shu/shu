import React from 'react';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Paper,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import NavigateBeforeIcon from '@mui/icons-material/NavigateBefore';
import NavigateNextIcon from '@mui/icons-material/NavigateNext';
import { useQuery } from 'react-query';
import { extractDataFromResponse, knowledgeBaseAPI } from '../services/api';
import { getSurfaceColor } from '../utils/constants';

/**
 * Modal for viewing full chunk content with navigation between chunks.
 *
 * Props:
 * - open: boolean - Whether the modal is open
 * - onClose: () => void - Called when modal should close
 * - knowledgeBaseId: string - KB ID for fetching chunks
 * - documentId: string - Document ID for fetching chunks
 * - documentTitle: string - Document title for display
 * - initialChunk: object - Initial chunk data from search results (has chunk_id, chunk_index, surface, score, snippet, summary)
 */
export default function ChunkDetailModal({ open, onClose, knowledgeBaseId, documentId, documentTitle, initialChunk }) {
  const [currentIndex, setCurrentIndex] = React.useState(initialChunk?.chunk_index ?? 0);
  // Window anchor - only moves when navigating outside loaded range
  const [windowAnchor, setWindowAnchor] = React.useState(initialChunk?.chunk_index ?? 0);

  // Reset to initial chunk when modal opens with new chunk
  React.useEffect(() => {
    if (open && initialChunk) {
      const idx = initialChunk.chunk_index ?? 0;
      setCurrentIndex(idx);
      setWindowAnchor(idx);
    }
  }, [open, initialChunk]);

  // Fetch a small window of chunks centered on the anchor
  // Window size: 3 before + anchor + 3 after = 7 chunks
  const windowSize = 7;
  const windowOffset = Math.max(0, windowAnchor - 3);

  const {
    data: chunksData,
    isLoading,
    error,
  } = useQuery(
    ['document-chunks', knowledgeBaseId, documentId, windowOffset],
    () =>
      knowledgeBaseAPI
        .getDocumentChunks(knowledgeBaseId, documentId, { limit: windowSize, offset: windowOffset })
        .then(extractDataFromResponse),
    {
      enabled: open && !!knowledgeBaseId && !!documentId,
      staleTime: 30000, // Cache for 30 seconds
      keepPreviousData: true, // Keep showing previous data while fetching new window
    }
  );

  const chunks = chunksData?.items || [];
  // Total comes from the API response only — never seed from search-result counts
  // which reflect contributing hits, not the document's real chunk count.
  const total = chunksData?.total ?? null;

  // Loaded range based on actual response
  const loadedStart = windowOffset;
  const loadedEnd = windowOffset + chunks.length - 1;

  // Get current chunk from fetched data, or fall back to initial chunk data
  const currentChunk = chunks.find((c) => c.chunk_index === currentIndex) || null;

  // Use fetched content if available. Only fall back to initialChunk when
  // viewing the originally clicked chunk — otherwise we'd show stale content
  // from a different chunk during window shifts (keepPreviousData is true).
  const isInitialIndex = currentIndex === (initialChunk?.chunk_index ?? -1);
  const content = currentChunk?.content || (isInitialIndex ? initialChunk?.snippet : '') || '';
  const summary = currentChunk?.summary || (isInitialIndex ? initialChunk?.summary : null) || null;

  const canGoPrev = currentIndex > 0;
  const canGoNext = total !== null && currentIndex < total - 1;

  const handlePrev = () => {
    if (canGoPrev) {
      const newIndex = currentIndex - 1;
      setCurrentIndex(newIndex);
      // Move window if approaching edge (within 1 of loaded boundary)
      if (newIndex <= loadedStart && newIndex > 0) {
        setWindowAnchor(newIndex);
      }
    }
  };

  const handleNext = () => {
    if (canGoNext) {
      const newIndex = currentIndex + 1;
      setCurrentIndex(newIndex);
      // Move window if approaching edge (within 1 of loaded boundary)
      if (newIndex >= loadedEnd && (total === null || newIndex < total - 1)) {
        setWindowAnchor(newIndex);
      }
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pr: 6 }}>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Box>
            <Typography variant="h6" component="span">
              Chunk #{currentIndex}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {documentTitle}
            </Typography>
          </Box>
          <IconButton onClick={onClose} sx={{ position: 'absolute', right: 8, top: 8 }} aria-label="close">
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent dividers>
        {/* Metadata chips — surface/score only shown for the initially clicked chunk */}
        {(initialChunk || currentChunk) && (
          <Box display="flex" gap={1} flexWrap="wrap" mb={2}>
            {isInitialIndex && initialChunk?.surface && (
              <Chip
                label={`Surface: ${initialChunk.surface}`}
                size="small"
                color={getSurfaceColor(initialChunk.surface)}
                variant="outlined"
              />
            )}
            {isInitialIndex && typeof initialChunk?.score === 'number' && (
              <Chip
                label={`Score: ${(initialChunk.score * 100).toFixed(1)}%`}
                size="small"
                color="primary"
                variant="outlined"
              />
            )}
            {currentChunk && (
              <>
                <Chip
                  label={`${currentChunk.char_count?.toLocaleString() || '?'} chars`}
                  size="small"
                  variant="outlined"
                />
                {typeof currentChunk.start_char === 'number' && typeof currentChunk.end_char === 'number' && (
                  <Chip
                    label={`Pos: ${currentChunk.start_char.toLocaleString()}-${currentChunk.end_char.toLocaleString()}`}
                    size="small"
                    variant="outlined"
                  />
                )}
              </>
            )}
          </Box>
        )}

        {/* Loading state */}
        {isLoading && (
          <Box display="flex" justifyContent="center" py={4}>
            <CircularProgress />
          </Box>
        )}

        {/* Error state */}
        {error && (
          <Paper sx={{ p: 2, bgcolor: 'error.light', color: 'error.contrastText' }}>
            <Typography>Failed to load chunk content</Typography>
          </Paper>
        )}

        {/* Summary section */}
        {summary && !isLoading && (
          <Box mb={2}>
            <Typography variant="subtitle2" color="text.secondary" gutterBottom>
              Summary
            </Typography>
            <Paper variant="outlined" sx={{ p: 1.5, bgcolor: 'action.hover' }}>
              <Typography variant="body2" sx={{ fontStyle: 'italic' }}>
                {summary}
              </Typography>
            </Paper>
          </Box>
        )}

        {/* Content section */}
        {!isLoading && (
          <Box>
            <Typography variant="subtitle2" color="text.secondary" gutterBottom>
              Content
            </Typography>
            <Paper
              variant="outlined"
              sx={{
                p: 2,
                maxHeight: 400,
                overflow: 'auto',
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
                  lineHeight: 1.6,
                }}
              >
                {content || 'No content available.'}
              </Typography>
            </Paper>
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ justifyContent: 'space-between', px: 2 }}>
        {/* Navigation */}
        <Box display="flex" alignItems="center" gap={1}>
          <IconButton onClick={handlePrev} disabled={!canGoPrev || isLoading} size="small" aria-label="Previous chunk">
            <NavigateBeforeIcon />
          </IconButton>
          <Typography variant="body2" color="text.secondary">
            {total !== null ? `${currentIndex + 1} of ${total}` : `#${currentIndex}`}
          </Typography>
          <IconButton onClick={handleNext} disabled={!canGoNext || isLoading} size="small" aria-label="Next chunk">
            <NavigateNextIcon />
          </IconButton>
        </Box>

        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
