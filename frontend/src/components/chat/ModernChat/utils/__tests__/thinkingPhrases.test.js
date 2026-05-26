import { describe, it, expect } from 'vitest';
import { DEFAULT_POOL, RAG_POOL, PLUGIN_POOL, getPoolFor, pickNextWord, derivePool } from '../thinkingPhrases';

describe('thinkingPhrases', () => {
  describe('getPoolFor', () => {
    it('returns RAG_POOL for "rag"', () => {
      expect(getPoolFor('rag')).toBe(RAG_POOL);
    });

    it('returns PLUGIN_POOL for "plugin"', () => {
      expect(getPoolFor('plugin')).toBe(PLUGIN_POOL);
    });

    it('returns DEFAULT_POOL for "default"', () => {
      expect(getPoolFor('default')).toBe(DEFAULT_POOL);
    });

    it('falls back to DEFAULT_POOL for unknown keys', () => {
      expect(getPoolFor('something-else')).toBe(DEFAULT_POOL);
      expect(getPoolFor(undefined)).toBe(DEFAULT_POOL);
      expect(getPoolFor(null)).toBe(DEFAULT_POOL);
      expect(getPoolFor('')).toBe(DEFAULT_POOL);
    });
  });

  describe('pickNextWord', () => {
    it('returns a word that is a member of the pool', () => {
      const word = pickNextWord(DEFAULT_POOL, null);
      expect(DEFAULT_POOL).toContain(word);
    });

    it('never returns currentWord on a multi-element pool', () => {
      const currentWord = 'Wafting';
      for (let i = 0; i < 200; i += 1) {
        expect(pickNextWord(DEFAULT_POOL, currentWord)).not.toBe(currentWord);
      }
    });

    it('returns the only element of a single-element pool, even when it matches currentWord', () => {
      expect(pickNextWord(['Alone'], 'Alone')).toBe('Alone');
      expect(pickNextWord(['Alone'], null)).toBe('Alone');
    });

    it('returns an empty string for an empty pool', () => {
      expect(pickNextWord([], null)).toBe('');
    });

    it('returns an empty string when pool is not an array', () => {
      expect(pickNextWord(null, null)).toBe('');
      expect(pickNextWord(undefined, null)).toBe('');
    });
  });

  describe('derivePool', () => {
    it('returns "plugin" when a plugin is selected, regardless of KB state', () => {
      expect(derivePool({ selectedPlugin: { name: 'foo' }, selectedKBIds: ['kb1'] })).toBe('plugin');
      expect(derivePool({ selectedPlugin: 'foo-id', selectedKBIds: [] })).toBe('plugin');
      expect(derivePool({ selectedPlugin: { name: 'foo' } })).toBe('plugin');
    });

    it('returns "rag" when at least one KB is selected and no plugin', () => {
      expect(derivePool({ selectedPlugin: null, selectedKBIds: ['kb1'] })).toBe('rag');
      expect(derivePool({ selectedKBIds: ['kb1', 'kb2'] })).toBe('rag');
    });

    it('returns "default" when neither a plugin nor a KB is selected', () => {
      expect(derivePool({})).toBe('default');
      expect(derivePool()).toBe('default');
      expect(derivePool({ selectedPlugin: null, selectedKBIds: [] })).toBe('default');
      expect(derivePool({ selectedKBIds: null })).toBe('default');
    });

    it('treats an array of only falsy entries as no KB selected', () => {
      expect(derivePool({ selectedKBIds: [null, undefined, ''] })).toBe('default');
      expect(derivePool({ selectedKBIds: [false, 0, ''] })).toBe('default');
    });
  });
});
