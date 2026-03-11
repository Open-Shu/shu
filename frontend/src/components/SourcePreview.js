import React from 'react';
import { Box, Typography, Paper, Chip, Divider, Card, CardContent, Collapse, IconButton } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

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
  return item && typeof item.surface_scores === 'object' && typeof item.final_score === 'number';
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
function MultiSurfaceItem({ result, rank }) {
  const [expanded, setExpanded] = React.useState(true);
  const chunks = result.contributing_chunks || [];

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
          {Object.entries(result.surface_scores || {}).map(([surface, score]) => (
            <Chip
              key={surface}
              label={`${surface}: ${(score * 100).toFixed(1)}%`}
              variant="outlined"
              size="small"
              color={surface === 'chunk_vector' ? 'info' : 'secondary'}
            />
          ))}
        </Box>
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
                {chunks.slice(0, 5).map((chunk) => (
                  <Box
                    key={chunk.chunk_id}
                    sx={{
                      bgcolor: 'grey.100',
                      p: 1,
                      borderRadius: 1,
                      mb: 1,
                      fontSize: '0.875rem',
                    }}
                  >
                    <Box display="flex" alignItems="center" gap={1} mb={0.5}>
                      <Chip
                        label={`#${chunk.chunk_index}`}
                        size="small"
                        color="default"
                        sx={{ fontWeight: 'bold', minWidth: 45 }}
                      />
                      <Chip
                        label={chunk.surface}
                        size="small"
                        variant="outlined"
                        color={chunk.surface === 'chunk_vector' ? 'info' : 'secondary'}
                      />
                      <Chip label={`${(chunk.score * 100).toFixed(1)}%`} size="small" variant="outlined" />
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
                {chunks.length > 5 && (
                  <Typography variant="caption" color="text.secondary">
                    +{chunks.length - 5} more chunks
                  </Typography>
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
function GroupedDocumentItem({ group, rank }) {
  const [expanded, setExpanded] = React.useState(true);
  const chunks = group.chunks.sort((a, b) => b.score - a.score);

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
            {chunks.slice(0, 5).map((chunk, idx) => {
              const chunkIndex = safeGet(chunk, ['chunk_index', 'index'], idx);
              const snippet = safeGet(chunk, ['snippet', 'content_snippet', 'content_preview', 'content'], '');
              const url = safeGet(chunk, ['source_url', 'url', 'link'], '');

              return (
                <Box
                  key={chunk.id || idx}
                  sx={{
                    bgcolor: 'grey.100',
                    p: 1,
                    borderRadius: 1,
                    mb: 1,
                    fontSize: '0.875rem',
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
            {chunks.length > 5 && (
              <Typography variant="caption" color="text.secondary">
                +{chunks.length - 5} more chunks
              </Typography>
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
 */
export default function SourcePreview({ title = 'Sources', sources = [], searchQuery = '', groupByDoc = true }) {
  const items = Array.isArray(sources) ? sources : [];
  const isMultiSurface = items.length > 0 && isMultiSurfaceResult(items[0]);

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
            <MultiSurfaceItem key={result.document_id} result={result} rank={idx + 1} />
          ))}
        </Box>
      ) : groupByDoc ? (
        // Regular results grouped by document
        <Box>
          {groupByDocument(items)
            .slice(0, 20)
            .map((group, idx) => (
              <GroupedDocumentItem key={group.document_id} group={group} rank={idx + 1} />
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
    </Box>
  );
}
