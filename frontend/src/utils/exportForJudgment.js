/**
 * Formats baseline (similarity) and multi-surface search results side by side
 * for LLM judgment. Both sides use identical document-grouped formatting with
 * individually labeled chunks to avoid structural bias in the judge's evaluation.
 *
 * Multi-surface results use the backend's formatted_results (SHU-652) which
 * handles deduplication, annotation, title-chunk filtering, and chunk promotion.
 */

/**
 * Group baseline results (one chunk per row) by document, preserving
 * the best chunk score as the document score.
 */
function groupBaselineByDocument(results) {
  const groups = new Map();
  for (const result of results) {
    const docId = result.document_id;
    if (!groups.has(docId)) {
      groups.set(docId, {
        document_title: result.document_title,
        score: result.similarity_score ?? 0,
        chunks: [],
      });
    }
    const group = groups.get(docId);
    if ((result.similarity_score ?? 0) > group.score) {
      group.score = result.similarity_score ?? 0;
    }
    group.chunks.push({
      score: result.similarity_score ?? 0,
      content: result.content || '(no content)',
    });
  }
  return Array.from(groups.values());
}

/**
 * Format a single chunk with annotations.
 */
function formatChunk(chunk, index) {
  const scoreSuffix = chunk.promoted ? ', promoted — document-level match' : '';
  const lines = [`### Chunk ${index} (score: ${(chunk.score ?? 0).toFixed(4)}${scoreSuffix})`];

  if (chunk.matched_query) {
    lines.push(`> Matched query: "${chunk.matched_query}"`);
  }
  if (chunk.summary) {
    lines.push(`> Summary: ${chunk.summary}`);
  }

  lines.push('');
  lines.push(chunk.content || '(no content)');
  return lines.join('\n');
}

/**
 * Format a document with its chunks. Used for both baseline and MS results.
 */
function formatDocument(doc, rank, chunks, { showSurfaces, surfaceScores, synopsis, titleSummary } = {}) {
  const lines = [`## Document ${rank}: ${doc.document_title} (score: ${doc.score.toFixed(4)})`];

  if (titleSummary) {
    lines.push(`> ${titleSummary}`);
  }

  if (showSurfaces && surfaceScores) {
    const surfaces = Object.entries(surfaceScores)
      .map(([s, v]) => `${s}=${v.toFixed(4)}`)
      .join(', ');
    lines.push(`Surfaces: ${surfaces}`);
  }

  if (synopsis) {
    lines.push('');
    lines.push(`> Synopsis: ${synopsis}`);
  }

  for (let i = 0; i < chunks.length; i++) {
    lines.push('');
    lines.push(formatChunk(chunks[i], i + 1));
  }

  if (chunks.length === 0 && !synopsis) {
    lines.push('');
    lines.push('(no chunks retrieved for this document)');
  }

  return lines.join('\n');
}

/**
 * @param {string} query - The search query
 * @param {Array} baselineResults - Results from similarity search (QueryResult[])
 * @param {Array} formattedResults - Results from formatted_results (FormattedDocument[])
 * @param {number} [topN=10] - Number of documents per ranking
 * @returns {string} Formatted markdown for LLM judgment
 */
export function formatResultsForJudgment(query, baselineResults, formattedResults, topN = 10) {
  if ((!baselineResults || baselineResults.length === 0) && (!formattedResults || formattedResults.length === 0)) {
    return '';
  }

  // Group baseline chunks by document
  const baselineDocs = groupBaselineByDocument(baselineResults || []).slice(0, topN);

  // MS results are already formatted by the backend (SHU-652)
  const msRanked = (formattedResults || []).slice(0, topN);

  const baselineFormatted = baselineDocs.map((doc, i) => formatDocument(doc, i + 1, doc.chunks));

  const msFormatted = msRanked.map((doc, i) =>
    formatDocument({ document_title: doc.document_title, score: doc.final_score }, i + 1, doc.chunks || [], {
      showSurfaces: true,
      surfaceScores: doc.surface_scores,
      synopsis: doc.synopsis,
      titleSummary: doc.title_summary,
    })
  );

  const sections = [
    '# Query',
    query,
    '',
    '# Baseline Results (chunk similarity only)',
    ...baselineFormatted,
    '',
    '# Multi-Surface Results (fused ranking)',
    ...msFormatted,
    '',
    '# Judgment Prompt',
    '',
    'Please evaluate these two result sets on the following criteria.',
    'Each document may contain multiple retrieved chunks.',
    'Multi-surface results include annotations showing what was matched',
    '(synopsis, matched query, summary) — use these to understand why',
    'each document was retrieved, but judge on the chunk content itself.',
    '',
    '## 1. Retrieval Relevance (traditional IR)',
    'Which set contains more documents that are relevant to the query?',
    'Which set ranks the most relevant documents higher?',
    '',
    '## 2. Answer Utility',
    'If these chunks were the only context available to answer the query,',
    'which set better equips you to give a thorough, accurate answer?',
    'What information is available in one set but missing from the other?',
    '',
    '## Judgment',
    'For each criterion, state which set is better (Baseline, Multi-Surface, or Tie)',
    'with brief reasoning.',
    '',
    '## Output Format',
    '',
    'Include your full reasoning above, then end your response with a structured',
    'verdict block exactly like this (copy the template, fill in values):',
    '',
    '```verdict',
    'judge_model: [your model name and version]',
    'retrieval_relevance: [Baseline | Multi-Surface | Tie]',
    'answer_utility: [Baseline | Multi-Surface | Tie]',
    'overall: [Baseline | Multi-Surface | Tie]',
    'confidence: [high | medium | low]',
    'notes: [one sentence on the key differentiator]',
    '```',
  ];

  return sections.join('\n');
}
