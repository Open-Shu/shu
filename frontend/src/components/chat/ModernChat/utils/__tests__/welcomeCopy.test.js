/**
 * Tests for the welcome personality layer copy utilities (SHU-873).
 *
 * Covers the deterministic helpers where real bugs hide: client-side greeting
 * name derivation (the backend exposes only `user.name`) and `pickFresh`'s
 * cross-session de-dup + graceful degradation when localStorage is unavailable.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { GREETINGS, STARTER_CHIPS, getGreetingName, pickFresh } from '../welcomeCopy';

describe('getGreetingName', () => {
  it('returns the first token of a full name', () => {
    expect(getGreetingName({ name: 'Eric Longville' })).toBe('Eric');
  });

  it('returns a single-token name as-is', () => {
    expect(getGreetingName({ name: 'Madonna' })).toBe('Madonna');
  });

  it('prefers name over email', () => {
    expect(getGreetingName({ name: 'Jane Doe', email: 'jd@example.com' })).toBe('Jane');
  });

  it('falls back to the email local-part when name is blank/whitespace', () => {
    expect(getGreetingName({ name: '   ', email: 'jane.doe@example.com' })).toBe('jane.doe');
    expect(getGreetingName({ email: 'solo@example.com' })).toBe('solo');
  });

  it('returns empty string when nothing usable is present', () => {
    expect(getGreetingName(null)).toBe('');
    expect(getGreetingName(undefined)).toBe('');
    expect(getGreetingName({})).toBe('');
    expect(getGreetingName({ name: '', email: '' })).toBe('');
    expect(getGreetingName({ email: 'not-an-email' })).toBe('');
  });

  it('clamps a very long single-token name with an ellipsis', () => {
    const out = getGreetingName({ name: 'Bartholomewlongnameindeedxxxxxx' });
    expect(out.length).toBeLessThanOrEqual(23); // 22 chars + ellipsis
    expect(out.endsWith('…')).toBe(true);
  });
});

describe('pickFresh', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('returns a member of the pool', () => {
    expect(['a', 'b', 'c']).toContain(pickFresh(['a', 'b', 'c'], 'k1'));
  });

  it('returns `count` distinct members as an array', () => {
    const out = pickFresh(['a', 'b', 'c', 'd'], 'k2', { count: 3 });
    expect(Array.isArray(out)).toBe(true);
    expect(out).toHaveLength(3);
    expect(new Set(out).size).toBe(3);
  });

  it('does not repeat the previous selection back-to-back', () => {
    const first = pickFresh(['a', 'b'], 'k3');
    const second = pickFresh(['a', 'b'], 'k3');
    expect(second).not.toBe(first);
  });

  it('returns null / [] for an empty pool', () => {
    expect(pickFresh([], 'k4')).toBeNull();
    expect(pickFresh([], 'k5', { count: 2 })).toEqual([]);
    expect(pickFresh(null, 'k6')).toBeNull();
  });

  it('uses identify() for object pools and de-dups by id', () => {
    const pool = [{ id: 'a' }, { id: 'b' }];
    const identify = (x) => x.id;
    const first = pickFresh(pool, 'k7', { identify });
    const second = pickFresh(pool, 'k7', { identify });
    expect(first.id).not.toBe(second.id);
  });

  it('degrades to a plain pick (no throw) when localStorage is unavailable', () => {
    const getSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage denied');
    });
    const setSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage denied');
    });

    let out;
    expect(() => {
      out = pickFresh(['a', 'b', 'c'], 'k8');
    }).not.toThrow();
    expect(['a', 'b', 'c']).toContain(out);

    getSpy.mockRestore();
    setSpy.mockRestore();
  });
});

describe('copy pools', () => {
  it('greeting templates carry a {name} slot and a name-free anon fallback', () => {
    GREETINGS.forEach((g) => {
      expect(g.named).toContain('{name}');
      expect(g.anon).not.toContain('{name}');
    });
  });

  it('starter chips each have a non-empty label and prompt', () => {
    expect(STARTER_CHIPS.length).toBeGreaterThanOrEqual(4);
    STARTER_CHIPS.forEach((c) => {
      expect(typeof c.label).toBe('string');
      expect(c.label.length).toBeGreaterThan(0);
      expect(typeof c.prompt).toBe('string');
      expect(c.prompt.length).toBeGreaterThan(0);
    });
  });
});
