import { act, renderHook, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import usePersonalKB, { resolvePersonalKBName } from '../usePersonalKB';

vi.mock('../../../../../hooks/useAuth', () => ({
  useAuth: vi.fn(),
}));

vi.mock('../../../../../services/api', () => ({
  knowledgeBaseAPI: {
    list: vi.fn(),
    create: vi.fn(),
    uploadDocuments: vi.fn(),
  },
  // Pass-through helpers that mirror what the production helpers do for the
  // shapes our tests pass through them.
  extractDataFromResponse: (response) => {
    if (response && typeof response === 'object' && 'data' in response) {
      const first = response.data;
      if (first && typeof first === 'object' && 'data' in first) {
        return first.data;
      }
      return first;
    }
    return response;
  },
  extractItemsFromResponse: (response) => {
    if (response && typeof response === 'object' && 'data' in response) {
      const first = response.data;
      if (first && typeof first === 'object' && 'data' in first) {
        return first.data?.items || [];
      }
      return first?.items || [];
    }
    return [];
  },
}));

vi.mock('../../../../../utils/log', () => ({
  log: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

import { useAuth } from '../../../../../hooks/useAuth';
import { knowledgeBaseAPI } from '../../../../../services/api';
import { log } from '../../../../../utils/log';

describe('resolvePersonalKBName', () => {
  describe('happy paths — name precedence', () => {
    it('uses first + last name for a two-token name', () => {
      expect(resolvePersonalKBName({ name: 'Eric Longville' })).toBe("Eric Longville's Knowledge");
    });

    it('uses single-token names as-is', () => {
      expect(resolvePersonalKBName({ name: 'Madonna' })).toBe("Madonna's Knowledge");
    });

    it('drops middle names; keeps first + last', () => {
      expect(resolvePersonalKBName({ name: 'Eric David Longville' })).toBe("Eric Longville's Knowledge");
    });

    it('preserves unicode in first and last names', () => {
      expect(resolvePersonalKBName({ name: 'José García' })).toBe("José García's Knowledge");
    });

    it('strips leading and trailing whitespace before tokenizing', () => {
      expect(resolvePersonalKBName({ name: '  Eric Longville  ' })).toBe("Eric Longville's Knowledge");
    });

    it('treats internal multiple spaces as a single delimiter', () => {
      expect(resolvePersonalKBName({ name: 'Eric    Longville' })).toBe("Eric Longville's Knowledge");
    });

    it('prefers name over email when both are present', () => {
      expect(resolvePersonalKBName({ name: 'Eric Longville', email: 'someone-else@example.com' })).toBe(
        "Eric Longville's Knowledge"
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

describe('usePersonalKB hook', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ user: { name: 'Test User', email: 'test@example.com' } });
  });

  const listResponse = (items) => ({ data: { data: { items } } });
  const createResponse = (kb) => ({ data: { data: kb } });
  const uploadResponse = (results) => ({ data: { data: { results } } });

  it('finds an existing Personal KB on mount', async () => {
    const personal = { id: 'kb-pk', name: "Test User's Knowledge", is_personal: true, document_count: 2 };
    knowledgeBaseAPI.list.mockResolvedValueOnce(
      listResponse([{ id: 'kb-other', name: 'Project', is_personal: false }, personal])
    );

    const { result } = renderHook(() => usePersonalKB());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.personalKB).toEqual(personal);
  });

  it('auto-provisions a Personal KB on first upload when none exists', async () => {
    knowledgeBaseAPI.list.mockResolvedValueOnce(listResponse([]));
    const created = { id: 'kb-new', name: "Test User's Knowledge", is_personal: true, document_count: 0 };
    knowledgeBaseAPI.create.mockResolvedValueOnce(createResponse(created));
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce(uploadResponse([{ filename: 'doc.pdf', success: true }]));
    knowledgeBaseAPI.list.mockResolvedValueOnce(listResponse([{ ...created, document_count: 1 }]));

    const { result } = renderHook(() => usePersonalKB());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.personalKB).toBeNull();

    const file = new File(['x'], 'doc.pdf');
    await act(async () => {
      await result.current.uploadFiles([file]);
    });

    expect(knowledgeBaseAPI.create).toHaveBeenCalledTimes(1);
    expect(knowledgeBaseAPI.create).toHaveBeenCalledWith({ name: "Test User's Knowledge", is_personal: true });
    expect(result.current.errors).toEqual([]);
  });

  it('deduplicates concurrent ensurePersonalKB calls — only one create', async () => {
    knowledgeBaseAPI.list.mockResolvedValueOnce(listResponse([]));

    let resolveCreate;
    knowledgeBaseAPI.create.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveCreate = resolve;
      })
    );
    knowledgeBaseAPI.uploadDocuments.mockResolvedValue(uploadResponse([{ filename: 'a.pdf', success: true }]));
    knowledgeBaseAPI.list.mockResolvedValue(
      listResponse([{ id: 'kb-new', name: "Test User's Knowledge", is_personal: true, document_count: 1 }])
    );

    const { result } = renderHook(() => usePersonalKB());
    await waitFor(() => expect(result.current.loading).toBe(false));

    const fileA = new File(['a'], 'a.pdf');
    const fileB = new File(['b'], 'b.pdf');

    // Fire two upload calls before create resolves.
    let p1, p2;
    await act(async () => {
      p1 = result.current.uploadFiles([fileA]);
      p2 = result.current.uploadFiles([fileB]);
      // Now resolve the in-flight create so both pending uploads can proceed.
      resolveCreate(
        createResponse({
          id: 'kb-new',
          name: "Test User's Knowledge",
          is_personal: true,
          document_count: 0,
        })
      );
      await Promise.all([p1, p2]);
    });

    // Despite two concurrent uploadFiles calls, ensurePersonalKB should have
    // dedup'd to a single create.
    expect(knowledgeBaseAPI.create).toHaveBeenCalledTimes(1);
  });

  it('records per-file errors on partial failure and clears them on retry success', async () => {
    const personal = { id: 'kb-pk', name: "Test User's Knowledge", is_personal: true, document_count: 0 };
    knowledgeBaseAPI.list.mockResolvedValue(listResponse([personal]));
    // First upload: doc.pdf fails.
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce(
      uploadResponse([{ filename: 'doc.pdf', success: false, error: 'Unsupported file type' }])
    );
    // Retry of doc.pdf succeeds.
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce(uploadResponse([{ filename: 'doc.pdf', success: true }]));

    const { result } = renderHook(() => usePersonalKB());
    await waitFor(() => expect(result.current.loading).toBe(false));

    const file = new File(['x'], 'doc.pdf');
    await act(async () => {
      await result.current.uploadFiles([file]);
    });
    expect(result.current.errors).toHaveLength(1);
    expect(result.current.errors[0].filename).toBe('doc.pdf');
    expect(result.current.errors[0].message).toBe('Unsupported file type');

    await act(async () => {
      await result.current.retryFile('doc.pdf');
    });
    // After successful retry, the error for that filename is gone.
    expect(result.current.errors).toEqual([]);
  });

  it('handles upload response in bare-array shape', async () => {
    const personal = { id: 'kb-pk', name: "Test User's Knowledge", is_personal: true, document_count: 0 };
    knowledgeBaseAPI.list.mockResolvedValue(listResponse([personal]));
    // Backend returns the per-file array directly (legacy shape).
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce({
      data: { data: [{ filename: 'a.pdf', success: true }] },
    });

    const { result } = renderHook(() => usePersonalKB());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.uploadFiles([new File(['a'], 'a.pdf')]);
    });

    expect(result.current.errors).toEqual([]);
    expect(log.warn).not.toHaveBeenCalled();
  });

  it('logs a warning when upload response shape is unrecognized', async () => {
    const personal = { id: 'kb-pk', name: "Test User's Knowledge", is_personal: true, document_count: 0 };
    knowledgeBaseAPI.list.mockResolvedValue(listResponse([personal]));
    // Neither bare array nor { results: [...] } — backend returned something else.
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce({
      data: { data: { unexpected: 'shape' } },
    });

    const { result } = renderHook(() => usePersonalKB());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.uploadFiles([new File(['a'], 'a.pdf')]);
    });

    expect(log.warn).toHaveBeenCalledWith(
      'usePersonalKB: unexpected upload response shape',
      expect.objectContaining({ unexpected: 'shape' })
    );
  });
});
