import React from 'react';
import { Box, Typography, Paper, Chip, Divider } from '@mui/material';

function safeGet(obj, keys, fallback = '') {
  for (const k of keys) {
    if (obj && obj[k] !== undefined && obj[k] !== null) {
      return obj[k];
    }
  }
  return fallback;
}

/**
 * Minimal source preview for KB query results.
 * Props:
 * - title?: string
 * - sources: Array<any>
 * - searchQuery?: string
 */
export default function SourcePreview({ title = 'Sources', sources = [], searchQuery = '' }) {
  const items = Array.isArray(sources) ? sources : [];

  return (
    <Box>
      <Box display="flex" alignItems="center" justifyContent="space-between" mb={1}>
        <Typography variant="subtitle1">{title}</Typography>
        <Chip label={`${items.length} item${items.length === 1 ? '' : 's'}`} size="small" color="primary" />
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
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {items.slice(0, 20).map((src, idx) => {
            const title = safeGet(src, ['document_title', 'title', 'name'], `Result ${idx + 1}`);
            const snippet = safeGet(src, ['snippet', 'content_snippet', 'content_preview', 'content'], '');
            const url = safeGet(src, ['source_url', 'url', 'link'], '');
            const score = safeGet(src, ['score', 'similarity', 'confidence'], null);

            return (
              <Paper key={idx} sx={{ p: 1.5, backgroundColor: 'grey.50' }}>
                <Typography variant="subtitle2" gutterBottom>
                  {title}
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
