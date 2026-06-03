import { describe, it, expect } from 'vitest';
import { docStage } from '../docStage';

// The 3-stage mapping has several branches and a content_processed sticky-Ready
// nuance (profiling-disabled docs); these cases catch a mis-mapping that the
// component/integration tests wouldn't isolate.
describe('docStage', () => {
  it('advances the Ingesting bar through pending(0) / extracting(1) / embedding(2)', () => {
    expect(docStage({ processing_status: 'pending' })).toEqual({ kind: 'progress', step: 0 });
    expect(docStage({ processing_status: 'extracting' })).toEqual({ kind: 'progress', step: 1 });
    expect(docStage({ processing_status: 'embedding' })).toEqual({ kind: 'progress', step: 2 });
  });

  it('falls back to the first Ingesting step for unknown / empty statuses', () => {
    [undefined, 'something_new'].forEach((status) => {
      expect(docStage({ processing_status: status })).toEqual({ kind: 'progress', step: 0 });
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

  it('defaults a null/empty document to Ingesting', () => {
    expect(docStage(null)).toEqual({ kind: 'progress', step: 0 });
    expect(docStage({})).toEqual({ kind: 'progress', step: 0 });
  });
});
