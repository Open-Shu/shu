import { describe, it, expect } from 'vitest';
import { docStage } from '../docStage';

// The 3-stage mapping has several branches and a content_processed sticky-Ready
// nuance (profiling-disabled docs); these cases catch a mis-mapping that the
// component/integration tests wouldn't isolate.
describe('docStage', () => {
  it('advances the bar + label through pending / extracting / embedding', () => {
    expect(docStage({ processing_status: 'pending' })).toEqual({ kind: 'progress', step: 0, label: 'Queued…' });
    expect(docStage({ processing_status: 'extracting' })).toEqual({
      kind: 'progress',
      step: 1,
      label: 'Extracting text…',
    });
    expect(docStage({ processing_status: 'embedding' })).toEqual({ kind: 'progress', step: 2, label: 'Indexing…' });
  });

  it('falls back to the generic Ingesting label for a truthy unknown / future status', () => {
    expect(docStage({ processing_status: 'something_new' })).toEqual({
      kind: 'progress',
      step: 0,
      label: 'Ingesting…',
    });
  });

  it('maps profiling / artifact_embedding to the additive Enhancing state (Ready stays sticky), carrying coverage', () => {
    // Profiling runs after content_processed (Ready), so it is non-blocking
    // 'enhancing', not a regression to a pre-Ready progress step (Decision 17).
    expect(docStage({ processing_status: 'profiling', profiling_coverage_percent: 42 })).toEqual({
      kind: 'enhancing',
      coverage: 42,
    });
    expect(docStage({ processing_status: 'artifact_embedding' })).toEqual({
      kind: 'enhancing',
      coverage: undefined,
    });
  });

  it('treats every terminal-success status as sticky Ready (incl. profiling-disabled content_processed)', () => {
    ['content_processed', 'rag_processed', 'profile_processed'].forEach((status) => {
      expect(docStage({ processing_status: status })).toEqual({ kind: 'ready' });
    });
  });

  it('maps error to Failed', () => {
    expect(docStage({ processing_status: 'error' })).toEqual({ kind: 'failed' });
  });

  it('defaults a null/empty/absent-status document to the first stage (Queued)', () => {
    // doc?.processing_status || 'pending' means missing/empty resolves to pending.
    expect(docStage(null)).toEqual({ kind: 'progress', step: 0, label: 'Queued…' });
    expect(docStage({})).toEqual({ kind: 'progress', step: 0, label: 'Queued…' });
    expect(docStage({ processing_status: undefined })).toEqual({ kind: 'progress', step: 0, label: 'Queued…' });
  });
});
