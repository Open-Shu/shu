import { resolvePersonalKBName } from '../usePersonalKB';

describe('resolvePersonalKBName', () => {
  describe('happy paths — name precedence', () => {
    it('uses first name from a multi-token name', () => {
      expect(resolvePersonalKBName({ name: 'Eric Longville' })).toBe("Eric's Knowledge");
    });

    it('uses single-token names as-is', () => {
      expect(resolvePersonalKBName({ name: 'Madonna' })).toBe("Madonna's Knowledge");
    });

    it('handles names with multiple whitespace tokens', () => {
      expect(resolvePersonalKBName({ name: 'Eric David Longville' })).toBe("Eric's Knowledge");
    });

    it('preserves unicode in first names', () => {
      expect(resolvePersonalKBName({ name: 'José García' })).toBe("José's Knowledge");
    });

    it('strips leading whitespace before extracting first token', () => {
      expect(resolvePersonalKBName({ name: '  Eric Longville  ' })).toBe("Eric's Knowledge");
    });

    it('treats internal multiple spaces as a single delimiter', () => {
      expect(resolvePersonalKBName({ name: 'Eric    Longville' })).toBe("Eric's Knowledge");
    });

    it('prefers name over email when both are present', () => {
      expect(resolvePersonalKBName({ name: 'Eric Longville', email: 'someone-else@example.com' })).toBe(
        "Eric's Knowledge"
      );
    });
  });

  describe('email fallback', () => {
    it('uses email local part when name is empty', () => {
      expect(resolvePersonalKBName({ name: '', email: 'user42@example.com' })).toBe("user42's Knowledge");
    });

    it('uses email local part when name is missing entirely', () => {
      expect(resolvePersonalKBName({ email: 'j.doe@example.com' })).toBe("j.doe's Knowledge");
    });

    it('uses email local part when name is whitespace only', () => {
      expect(resolvePersonalKBName({ name: '   ', email: 'eric@openshu.ai' })).toBe("eric's Knowledge");
    });

    it('keeps generic-looking local parts (admins still need to identify owner)', () => {
      expect(resolvePersonalKBName({ email: 'user1234@example.com' })).toBe("user1234's Knowledge");
    });

    it('uses local part even when no domain follows the @', () => {
      // 'foo@' contains '@' and split[0] = 'foo' (non-empty)
      expect(resolvePersonalKBName({ email: 'foo@' })).toBe("foo's Knowledge");
    });
  });

  describe('final fallback to "Personal Knowledge"', () => {
    it('handles null user', () => {
      expect(resolvePersonalKBName(null)).toBe('Personal Knowledge');
    });

    it('handles undefined user', () => {
      expect(resolvePersonalKBName(undefined)).toBe('Personal Knowledge');
    });

    it('handles empty user object', () => {
      expect(resolvePersonalKBName({})).toBe('Personal Knowledge');
    });

    it('handles null name and null email', () => {
      expect(resolvePersonalKBName({ name: null, email: null })).toBe('Personal Knowledge');
    });

    it('falls back when email has no @', () => {
      expect(resolvePersonalKBName({ email: 'no-at-sign' })).toBe('Personal Knowledge');
    });

    it('falls back when email has empty local part', () => {
      expect(resolvePersonalKBName({ email: '@example.com' })).toBe('Personal Knowledge');
    });

    it('falls back when email local part is whitespace only', () => {
      expect(resolvePersonalKBName({ email: '   @example.com' })).toBe('Personal Knowledge');
    });

    it('falls back when both name and email are present but unusable', () => {
      expect(resolvePersonalKBName({ name: '   ', email: '@example.com' })).toBe('Personal Knowledge');
    });
  });

  describe('garbage input — never throws', () => {
    it('does not throw on numeric name', () => {
      // Defensive: even unexpected types should be coerced gracefully.
      expect(() => resolvePersonalKBName({ name: 123 })).not.toThrow();
    });

    it('does not throw on boolean fields', () => {
      expect(() => resolvePersonalKBName({ name: false, email: true })).not.toThrow();
    });

    it('does not throw on array name', () => {
      expect(() => resolvePersonalKBName({ name: ['Eric'] })).not.toThrow();
    });
  });
});
