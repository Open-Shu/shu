import { act, renderHook, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import usePersonalKB from '../usePersonalKB';

vi.mock('../../../../../hooks/useAuth', () => ({
  useAuth: vi.fn(),
}));

vi.mock('../../../../../services/api', () => ({
  knowledgeBaseAPI: {
    list: vi.fn(),
    ensurePersonal: vi.fn(),
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

// Display-name precedence (firstName lastName / firstName / email local part /
// fallback) is now derived server-side by `resolve_personal_kb_name` in the
// backend service. Tests for that logic live in
// `backend/src/tests/unit/services/test_knowledge_base_service.py`.

describe('usePersonalKB hook', () => {
  const TEST_USER_ID = 'user-test-1';

  beforeEach(() => {
    vi.clearAllMocks();
    // Mirrors the production shape from /auth/me — User.to_dict() exposes the
    // id as `user_id`, not `id`. resolveUserId() in usePersonalKB normalises
    // both, but tests should pin the production shape so a regression there
    // surfaces here.
    useAuth.mockReturnValue({
      user: { user_id: TEST_USER_ID, name: 'Test User', email: 'test@example.com' },
    });
  });

  const listResponse = (items) => ({ data: { data: { items } } });
  const createResponse = (kb) => ({ data: { data: kb } });
  const uploadResponse = (results) => ({ data: { data: { results } } });

  it('finds an existing Personal KB on mount', async () => {
    const personal = {
      id: 'kb-pk',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 2,
    };
    knowledgeBaseAPI.list.mockResolvedValueOnce(
      listResponse([{ id: 'kb-other', name: 'Project', is_personal: false }, personal])
    );

    const { result } = renderHook(() => usePersonalKB());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.personalKB).toEqual(personal);
  });

  it("does not match another user's Personal KB even when admin sees it in the list", async () => {
    // Admin user's list endpoint returns every personal KB in the system, but
    // the hook must only attach the one owned by the current user.
    const someoneElse = {
      id: 'kb-other-pk',
      name: "Other Person's Knowledge",
      is_personal: true,
      owner_id: 'user-someone-else',
      document_count: 9,
    };
    knowledgeBaseAPI.list.mockResolvedValueOnce(listResponse([someoneElse]));

    const { result } = renderHook(() => usePersonalKB());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.personalKB).toBeNull();
  });

  it('auto-provisions a Personal KB on first upload when none exists', async () => {
    knowledgeBaseAPI.list.mockResolvedValueOnce(listResponse([]));
    const ensured = {
      id: 'kb-new',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 0,
    };
    knowledgeBaseAPI.ensurePersonal.mockResolvedValueOnce(createResponse(ensured));
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce(uploadResponse([{ filename: 'doc.pdf', success: true }]));
    knowledgeBaseAPI.list.mockResolvedValueOnce(listResponse([{ ...ensured, document_count: 1 }]));

    const { result } = renderHook(() => usePersonalKB());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.personalKB).toBeNull();

    const file = new File(['x'], 'doc.pdf');
    await act(async () => {
      await result.current.uploadFiles([file]);
    });

    // No client-supplied body — server derives is_personal and the display
    // name from the authenticated user's identity.
    expect(knowledgeBaseAPI.ensurePersonal).toHaveBeenCalledTimes(1);
    expect(knowledgeBaseAPI.ensurePersonal).toHaveBeenCalledWith();
    expect(result.current.errors).toEqual([]);
  });

  it('deduplicates concurrent ensurePersonalKB calls — only one ensure call', async () => {
    knowledgeBaseAPI.list.mockResolvedValueOnce(listResponse([]));

    let resolveEnsure;
    knowledgeBaseAPI.ensurePersonal.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveEnsure = resolve;
      })
    );
    knowledgeBaseAPI.uploadDocuments.mockResolvedValue(uploadResponse([{ filename: 'a.pdf', success: true }]));
    knowledgeBaseAPI.list.mockResolvedValue(
      listResponse([
        {
          id: 'kb-new',
          name: "Test User's Knowledge",
          is_personal: true,
          owner_id: TEST_USER_ID,
          document_count: 1,
        },
      ])
    );

    const { result } = renderHook(() => usePersonalKB());
    await waitFor(() => expect(result.current.loading).toBe(false));

    const fileA = new File(['a'], 'a.pdf');
    const fileB = new File(['b'], 'b.pdf');

    // Fire two upload calls before ensure resolves.
    let p1, p2;
    await act(async () => {
      p1 = result.current.uploadFiles([fileA]);
      p2 = result.current.uploadFiles([fileB]);
      // Now resolve the in-flight ensure so both pending uploads can proceed.
      resolveEnsure(
        createResponse({
          id: 'kb-new',
          name: "Test User's Knowledge",
          is_personal: true,
          owner_id: TEST_USER_ID,
          document_count: 0,
        })
      );
      await Promise.all([p1, p2]);
    });

    // Despite two concurrent uploadFiles calls, ensurePersonalKB should have
    // dedup'd to a single ensure call.
    expect(knowledgeBaseAPI.ensurePersonal).toHaveBeenCalledTimes(1);
  });

  it('records per-file errors on partial failure and clears them on retry success', async () => {
    const personal = {
      id: 'kb-pk',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 0,
    };
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
    const personal = {
      id: 'kb-pk',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 0,
    };
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
    const personal = {
      id: 'kb-pk',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 0,
    };
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
