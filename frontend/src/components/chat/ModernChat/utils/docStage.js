// Map a backend Document.processing_status into the user-facing stages
// (SHU-817 S3): Ingesting → Ready, with profiling surfaced as an additive,
// non-blocking "Enhancing" state, plus a terminal Failed.
//
// Profiling/artifact-embedding ALWAYS run after content_processed (a
// terminal-success / "Ready" state), so they are an enhancement layered on top
// of an already-usable doc — never a regression to a pre-Ready stage. Modeling
// them as 'enhancing' (distinct from 'progress') keeps "Ready" sticky: a doc
// that reaches Ready stays Ready and the brain badge doesn't flip back to
// indexing while profiling runs (Decision 17 / content-processed-flip). A
// profiling-disabled doc ends at content_processed and simply reads "Ready".
//
// Mirrors backend DocumentStatus enum (backend/src/shu/models/document.py).
export const TERMINAL_SUCCESS_STATUSES = new Set(['content_processed', 'rag_processed', 'profile_processed']);

// The pre-ready pipeline, in order, for the 3-segment StageBar fill: the journey
// to "searchable" (content_processed = Ready). Profiling is deliberately NOT here
// — it runs AFTER Ready and surfaces via the additive 'enhancing' state, so the
// bar only ever advances toward usable and never regresses once Ready.
const PROGRESS_STEP = { pending: 0, extracting: 1, embedding: 2 };

export const docStage = (doc) => {
  const status = doc?.processing_status || 'pending';
  if (status === 'error') {
    return { kind: 'failed' };
  }
  if (TERMINAL_SUCCESS_STATUSES.has(status)) {
    return { kind: 'ready' };
  }
  if (status === 'profiling' || status === 'artifact_embedding') {
    return { kind: 'enhancing', coverage: doc?.profiling_coverage_percent };
  }
  // Unknown / future pre-ready statuses fall back to the first segment.
  return { kind: 'progress', step: PROGRESS_STEP[status] ?? 0 };
};
