import React from 'react';
import { Box, Typography, Paper, Chip, Divider, Card, CardContent, Collapse, IconButton, Button } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import VisibilityIcon from '@mui/icons-material/Visibility';
import ChunkDetailModal from './ChunkDetailModal';

function safeGet(obj, keys, fallback = '') {
  for (const k of keys) {
    if (obj && obj[k] !== undefined && obj[k] !== null) {
      return obj[k];
    }
  }
  return fallback;
}

/**
 * Detect if this is a multi-surface result (has surface_scores and final_score)
 */
function isMultiSurfaceResult(item) {
  return (
    item &&
    item.surface_scores !== null &&
    typeof item.surface_scores === 'object' &&
    typeof item.final_score === 'number'
  );
}

/**
 * Group regular chunk results by document for cleaner display
 */
function groupByDocument(items) {
  const groups = new Map();
  for (const item of items) {
    const docId = item.document_id || 'unknown';
    if (!groups.has(docId)) {
      groups.set(docId, {
        document_id: docId,
        document_title: safeGet(item, ['document_title', 'title', 'name'], 'Unknown Document'),
        chunks: [],
        best_score: 0,
      });
    }
    const group = groups.get(docId);
    const score = safeGet(item, ['score', 'similarity_score', 'similarity', 'confidence'], 0);
    group.chunks.push({ ...item, score });
    if (score > group.best_score) {
      group.best_score = score;
    }
  }
  // Sort groups by best score descending
  return Array.from(groups.values()).sort((a, b) => b.best_score - a.best_score);
}

/**
 * Render a single multi-surface result (document with surface scores and contributing chunks)
 */
function MultiSurfaceItem({ result, rank, onChunkClick, showAllChunks = false }) {
  const [expanded, setExpanded] = React.useState(true);
  const [showAll, setShowAll] = React.useState(showAllChunks);
  const chunks = result.contributing_chunks || [];
  const displayChunks = showAll ? chunks : chunks.slice(0, 5);
  const hasMoreChunks = chunks.length > 5;

  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent sx={{ pb: expanded ? 1 : 2 }}>
        <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
          <Typography variant="subtitle1" fontWeight="bold">
            {rank}. {result.document_title}
          </Typography>
          <Chip label={`Score: ${(result.final_score * 100).toFixed(1)}%`} color="primary" size="small" />
        </Box>
        <Box display="flex" gap={1} flexWrap="wrap" mb={1}>
          {Object.entries(result.surface_scores || {}).map(([surface, score]) => {
            // Color coding for different surface types
            let color = 'secondary';
            if (surface === 'chunk_vector') {
              color = 'info';
            } else if (surface === 'query_match') {
              color = 'success';
            } else if (surface === 'bm25') {
              color = 'warning';
            } else if (surface === 'chunk_summary') {
              color = 'default';
            }
            return (
              <Chip
                key={surface}
                label={`${surface}: ${(score * 100).toFixed(1)}%`}
                variant="outlined"
                size="small"
                color={color}
              />
            );
          })}
        </Box>
        {/* Display matched query from query_match surface */}
        {result.surface_metadata?.query_match?.matched_query && (
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontStyle: 'italic' }}>
            Matched query: "{result.surface_metadata.query_match.matched_query}"
          </Typography>
        )}
        {chunks.length > 0 && (
          <Box>
            <Box display="flex" alignItems="center" sx={{ cursor: 'pointer' }} onClick={() => setExpanded(!expanded)}>
              <Typography variant="body2" color="text.secondary">
                {chunks.length} Contributing Chunk{chunks.length !== 1 ? 's' : ''}
              </Typography>
              <IconButton
                size="small"
                sx={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: '0.2s' }}
              >
                <ExpandMoreIcon fontSize="small" />
              </IconButton>
            </Box>
            <Collapse in={expanded}>
              <Box sx={{ mt: 1 }}>
                {displayChunks.map((chunk, index) => (
                  <Box
                    key={chunk.chunk_id || `chunk-${chunk.chunk_index ?? index}`}
                    onClick={() => onChunkClick && onChunkClick(chunk, result)}
                    onKeyDown={(e) => {
                      if (onChunkClick && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault();
                        onChunkClick(chunk, result);
                      }
                    }}
                    role={onChunkClick ? 'button' : undefined}
                    tabIndex={onChunkClick ? 0 : undefined}
                    sx={{
                      bgcolor: 'grey.100',
                      p: 1,
                      borderRadius: 1,
                      mb: 1,
                      fontSize: '0.875rem',
                      cursor: onChunkClick ? 'pointer' : 'default',
                      transition: 'background-color 0.15s',
                      '&:hover': onChunkClick
                        ? {
                            bgcolor: 'grey.200',
                          }
                        : {},
                    }}
                  >
                    <Box display="flex" alignItems="center" gap={1} mb={0.5}>
                      <Chip
                        label={`#${chunk.chunk_index ?? '?'}`}
                        size="small"
                        color="default"
                        sx={{ fontWeight: 'bold', minWidth: 45 }}
                      />
                      <Chip
                        label={chunk.surface || 'unknown'}
                        size="small"
                        variant="outlined"
                        color={chunk.surface === 'chunk_vector' ? 'info' : 'secondary'}
                      />
                      <Chip
                        label={typeof chunk.score === 'number' ? `${(chunk.score * 100).toFixed(1)}%` : 'N/A'}
                        size="small"
                        variant="outlined"
                      />
                      {onChunkClick && <VisibilityIcon fontSize="small" sx={{ ml: 'auto', color: 'action.active' }} />}
                    </Box>
                    <Typography variant="body2" sx={{ mt: 0.5 }}>
                      {chunk.snippet}
                    </Typography>
                    {chunk.summary && (
                      <Typography variant="caption" color="text.secondary" display="block" mt={0.5}>
                        Summary: {chunk.summary}
                      </Typography>
                    )}
                  </Box>
                ))}
                {hasMoreChunks && !showAll && (
                  <Button
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowAll(true);
                    }}
                    sx={{ mt: 0.5 }}
                  >
                    Show all {chunks.length} chunks
                  </Button>
                )}
                {hasMoreChunks && showAll && (
                  <Button
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowAll(false);
                    }}
                    sx={{ mt: 0.5 }}
                  >
                    Show fewer
                  </Button>
                )}
              </Box>
            </Collapse>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Render grouped regular results (chunks grouped by document)
 */
function GroupedDocumentItem({ group, rank, onChunkClick }) {
  const [expanded, setExpanded] = React.useState(true);
  const [showAll, setShowAll] = React.useState(false);
  const chunks = [...group.chunks].sort((a, b) => b.score - a.score);
  const displayChunks = showAll ? chunks : chunks.slice(0, 5);
  const hasMoreChunks = chunks.length > 5;

  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent sx={{ pb: expanded ? 1 : 2 }}>
        <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
          <Typography variant="subtitle1" fontWeight="bold">
            {rank}. {group.document_title}
          </Typography>
          <Chip label={`Best: ${(group.best_score * 100).toFixed(1)}%`} color="primary" size="small" />
        </Box>
        <Box display="flex" alignItems="center" sx={{ cursor: 'pointer' }} onClick={() => setExpanded(!expanded)}>
          <Typography variant="body2" color="text.secondary">
            {chunks.length} Chunk{chunks.length !== 1 ? 's' : ''}
          </Typography>
          <IconButton size="small" sx={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: '0.2s' }}>
            <ExpandMoreIcon fontSize="small" />
          </IconButton>
        </Box>
        <Collapse in={expanded}>
          <Box sx={{ mt: 1 }}>
            {displayChunks.map((chunk, idx) => {
              const chunkIndex = safeGet(chunk, ['chunk_index', 'index'], idx);
              const snippet = safeGet(chunk, ['snippet', 'content_snippet', 'content_preview', 'content'], '');
              const url = safeGet(chunk, ['source_url', 'url', 'link'], '');

              return (
                <Box
                  key={chunk.id || idx}
                  onClick={() => onChunkClick && onChunkClick(chunk, group)}
                  onKeyDown={(e) => {
                    if (onChunkClick && (e.key === 'Enter' || e.key === ' ')) {
                      e.preventDefault();
                      onChunkClick(chunk, group);
                    }
                  }}
                  role={onChunkClick ? 'button' : undefined}
                  tabIndex={onChunkClick ? 0 : undefined}
                  sx={{
                    bgcolor: 'grey.100',
                    p: 1,
                    borderRadius: 1,
                    mb: 1,
                    fontSize: '0.875rem',
                    cursor: onChunkClick ? 'pointer' : 'default',
                    transition: 'background-color 0.15s',
                    '&:hover': onChunkClick
                      ? {
                          bgcolor: 'grey.200',
                        }
                      : {},
                  }}
                >
                  <Box display="flex" alignItems="center" gap={1} mb={0.5}>
                    <Chip
                      label={`#${chunkIndex}`}
                      size="small"
                      color="default"
                      sx={{ fontWeight: 'bold', minWidth: 45 }}
                    />
                    <Chip label={`${(chunk.score * 100).toFixed(1)}%`} size="small" variant="outlined" />
                    {chunk.source_type && <Chip label={chunk.source_type} size="small" variant="outlined" />}
                    {onChunkClick && <VisibilityIcon fontSize="small" sx={{ ml: 'auto', color: 'action.active' }} />}
                  </Box>
                  {url && (
                    <Typography variant="caption" color="primary" sx={{ display: 'block', mb: 0.5 }}>
                      {url}
                    </Typography>
                  )}
                  {snippet && (
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                      {snippet.length > 300 ? `${snippet.slice(0, 300)}...` : snippet}
                    </Typography>
                  )}
                </Box>
              );
            })}
            {hasMoreChunks && !showAll && (
              <Button
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  setShowAll(true);
                }}
                sx={{ mt: 0.5 }}
              >
                Show all {chunks.length} chunks
              </Button>
            )}
            {hasMoreChunks && showAll && (
              <Button
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  setShowAll(false);
                }}
                sx={{ mt: 0.5 }}
              >
                Show fewer
              </Button>
            )}
          </Box>
        </Collapse>
      </CardContent>
    </Card>
  );
}

/**
 * Source preview for KB query results - supports both multi-surface and regular results.
 * Props:
 * - title?: string
 * - sources: Array<any> - regular chunk results or multi-surface results
 * - searchQuery?: string
 * - groupByDoc?: boolean - group regular results by document (default: true)
 * - knowledgeBaseId?: string - KB ID for fetching full chunk content (enables click-to-view)
 */
export default function SourcePreview({
  title = 'Sources',
  sources = [],
  searchQuery = '',
  groupByDoc = true,
  knowledgeBaseId = null,
}) {
  const items = Array.isArray(sources) ? sources : [];
  const isMultiSurface = items.length > 0 && isMultiSurfaceResult(items[0]);

  // State for chunk detail modal
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedChunk, setSelectedChunk] = React.useState(null);
  const [selectedDocument, setSelectedDocument] = React.useState(null);

  // Handle chunk click - open modal to view full content
  const handleChunkClick = React.useCallback(
    (chunk, docOrResult) => {
      if (!knowledgeBaseId) {
        return; // No KB ID means no modal functionality
      }

      const documentId = docOrResult.document_id || docOrResult.id;
      const chunkId = chunk.chunk_id || chunk.id;
      if (!documentId || !chunkId) {
        return; // Can't open modal without real identifiers
      }

      // Normalize chunk data for the modal
      const normalizedChunk = {
        chunk_id: chunkId,
        chunk_index: chunk.chunk_index ?? chunk.index,
        surface: chunk.surface || null,
        score: chunk.score ?? chunk.similarity_score ?? null,
        snippet: chunk.snippet || chunk.content_snippet || chunk.content_preview || chunk.content || '',
        summary: chunk.summary || null,
      };

      // Extract document info — totalChunks is unknown until the modal
      // fetches from the API; don't seed it from contributing_chunks.length
      // which only reflects search-result hits, not the document's real count.
      const docInfo = {
        document_id: documentId,
        document_title: docOrResult.document_title || docOrResult.title || 'Unknown Document',
      };

      setSelectedChunk(normalizedChunk);
      setSelectedDocument(docInfo);
      setModalOpen(true);
    },
    [knowledgeBaseId]
  );

  const handleModalClose = () => {
    setModalOpen(false);
    setSelectedChunk(null);
    setSelectedDocument(null);
  };

  // Count documents vs chunks for display
  const docCount = isMultiSurface ? items.length : groupByDoc ? groupByDocument(items).length : items.length;
  const itemLabel = isMultiSurface || groupByDoc ? 'document' : 'chunk';

  return (
    <Box>
      <Box display="flex" alignItems="center" justifyContent="space-between" mb={1}>
        <Typography variant="subtitle1">{title}</Typography>
        <Chip label={`${docCount} ${itemLabel}${docCount === 1 ? '' : 's'}`} size="small" color="primary" />
      </Box>
      {searchQuery && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          Search: {searchQuery}
        </Typography>
      )}
      <Divider sx={{ mb: 1 }} />
      {items.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No sources to display.
        </Typography>
      ) : isMultiSurface ? (
        // Multi-surface results - already grouped by document
        <Box>
          {items.slice(0, 20).map((result, idx) => (
            <MultiSurfaceItem
              key={result.document_id}
              result={result}
              rank={idx + 1}
              onChunkClick={knowledgeBaseId ? handleChunkClick : null}
            />
          ))}
        </Box>
      ) : groupByDoc ? (
        // Regular results grouped by document
        <Box>
          {groupByDocument(items)
            .slice(0, 20)
            .map((group, idx) => (
              <GroupedDocumentItem
                key={group.document_id}
                group={group}
                rank={idx + 1}
                onChunkClick={knowledgeBaseId ? handleChunkClick : null}
              />
            ))}
        </Box>
      ) : (
        // Flat list (legacy mode)
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {items.slice(0, 20).map((src, idx) => {
            const itemTitle = safeGet(src, ['document_title', 'title', 'name'], `Result ${idx + 1}`);
            const snippet = safeGet(src, ['snippet', 'content_snippet', 'content_preview', 'content'], '');
            const url = safeGet(src, ['source_url', 'url', 'link'], '');
            const score = safeGet(src, ['score', 'similarity_score', 'similarity', 'confidence'], null);

            return (
              <Paper key={idx} sx={{ p: 1.5, backgroundColor: 'grey.50' }}>
                <Typography variant="subtitle2" gutterBottom>
                  {itemTitle}
                </Typography>
                {url && (
                  <Typography variant="caption" color="primary" sx={{ display: 'block', mb: 0.5 }}>
                    {url}
                  </Typography>
                )}
                {snippet && (
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                    {snippet.length > 300 ? `${snippet.slice(0, 300)}...` : snippet}
                  </Typography>
                )}
                <Box sx={{ mt: 1, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                  {score !== null && (
                    <Chip
                      size="small"
                      label={`score: ${typeof score === 'number' ? score.toFixed(3) : score}`}
                      variant="outlined"
                    />
                  )}
                  {src.document_id && <Chip size="small" label={`doc: ${src.document_id}`} variant="outlined" />}
                  {src.source_type && <Chip size="small" label={`type: ${src.source_type}`} variant="outlined" />}
                </Box>
              </Paper>
            );
          })}
        </Box>
      )}

      {/* Chunk detail modal */}
      {knowledgeBaseId && selectedChunk && selectedDocument && (
        <ChunkDetailModal
          open={modalOpen}
          onClose={handleModalClose}
          knowledgeBaseId={knowledgeBaseId}
          documentId={selectedDocument.document_id}
          documentTitle={selectedDocument.document_title}
          initialChunk={selectedChunk}
        />
      )}
    </Box>
  );
}
