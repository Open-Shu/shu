// Map a backend Document.processing_status into the three user-facing stages
// (SHU-817 S3): Ingesting → Profiling → Ready, plus a terminal Failed. Any
// terminal-success value is sticky "Ready" so a doc never looks stuck — and a
// profiling-disabled doc (which ends at content_processed) still reads "Ready".
//
// Mirrors backend DocumentStatus enum (backend/src/shu/models/document.py).
export const TERMINAL_SUCCESS_STATUSES = new Set(['content_processed', 'rag_processed', 'profile_processed']);

export const docStage = (doc) => {
  const status = doc?.processing_status || 'pending';
  if (status === 'error') {
    return { kind: 'failed' };
  }
  if (TERMINAL_SUCCESS_STATUSES.has(status)) {
    return { kind: 'ready' };
  }
  if (status === 'profiling' || status === 'artifact_embedding') {
    return { kind: 'progress', step: 1, coverage: doc?.profiling_coverage_percent };
  }
  return { kind: 'progress', step: 0 };
};
