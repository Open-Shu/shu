import React from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { vi } from 'vitest';
import usePersonalKB from '../usePersonalKB';

vi.mock('../../../../../hooks/useAuth', () => ({
  useAuth: vi.fn(),
}));

vi.mock('../../../../../services/api', () => ({
  knowledgeBaseAPI: {
    getPersonal: vi.fn(),
    getDocuments: vi.fn(),
    ensurePersonal: vi.fn(),
    uploadDocuments: vi.fn(),
    deleteDocument: vi.fn(),
    reingestDocument: vi.fn(),
  },
  // Pass-through helpers that mirror what the production helpers do for the
  // (double-wrapped axios → envelope) shapes our tests pass through them.
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
}));

vi.mock('../../../../../utils/log', () => ({
  log: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

import { useAuth } from '../../../../../hooks/useAuth';
import { knowledgeBaseAPI } from '../../../../../services/api';
import { log } from '../../../../../utils/log';

// The Personal KB is now resolved server-side via GET /knowledge-bases/personal
// (owner-scoped) instead of list({limit:100}) + client-side ownership filter, so
// these tests mock getPersonal directly. Display-name precedence is derived
// server-side (test_knowledge_base_service.py).

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, cacheTime: 0 } },
  });
  // eslint-disable-next-line react/prop-types
  return function Wrapper({ children }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
};

const renderPersonalKB = () => renderHook(() => usePersonalKB(), { wrapper: makeWrapper() });

describe('usePersonalKB hook', () => {
  const TEST_USER_ID = 'user-test-1';

  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({
      user: { user_id: TEST_USER_ID, name: 'Test User', email: 'test@example.com' },
    });
    // Default: no documents (terminal/empty → the doc poll self-stops in tests).
    knowledgeBaseAPI.getDocuments.mockResolvedValue({ data: { data: { items: [], total: 0 } } });
  });

  // Axios responses are double-wrapped: response.data is the ShuResponse
  // envelope, envelope.data is the payload.
  const personalResponse = (kb) => ({ data: { data: kb } });
  const uploadResponse = (results) => ({ data: { data: { results } } });
  const docsResponse = (items) => ({ data: { data: { items, total: items.length } } });

  it('loads the Personal KB from GET /personal on mount', async () => {
    const personal = {
      id: 'kb-pk',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 2,
    };
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(personal));

    const { result } = renderPersonalKB();

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.personalKB).toEqual(personal);
    expect(knowledgeBaseAPI.getPersonal).toHaveBeenCalled();
  });

  it('shows no Personal KB when none is provisioned yet (null)', async () => {
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(null));

    const { result } = renderPersonalKB();

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.personalKB).toBeNull();
  });

  it('auto-provisions a Personal KB on first upload when none exists', async () => {
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(null));
    const ensured = {
      id: 'kb-new',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 0,
    };
    knowledgeBaseAPI.ensurePersonal.mockResolvedValueOnce(personalResponse(ensured));
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce(uploadResponse([{ filename: 'doc.pdf', success: true }]));

    const { result } = renderPersonalKB();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.personalKB).toBeNull();

    const file = new File(['x'], 'doc.pdf');
    await act(async () => {
      await result.current.uploadFiles([file]);
    });

    // No client-supplied body — the server derives is_personal + the display name.
    expect(knowledgeBaseAPI.ensurePersonal).toHaveBeenCalledTimes(1);
    expect(knowledgeBaseAPI.ensurePersonal).toHaveBeenCalledWith();
    expect(result.current.errors).toEqual([]);
  });

  it('deduplicates concurrent ensurePersonalKB calls — only one ensure call', async () => {
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(null));

    let resolveEnsure;
    knowledgeBaseAPI.ensurePersonal.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveEnsure = resolve;
      })
    );
    knowledgeBaseAPI.uploadDocuments.mockResolvedValue(uploadResponse([{ filename: 'a.pdf', success: true }]));

    const { result } = renderPersonalKB();
    await waitFor(() => expect(result.current.loading).toBe(false));

    const fileA = new File(['a'], 'a.pdf');
    const fileB = new File(['b'], 'b.pdf');

    let p1;
    let p2;
    await act(async () => {
      p1 = result.current.uploadFiles([fileA]);
      p2 = result.current.uploadFiles([fileB]);
      resolveEnsure(
        personalResponse({
          id: 'kb-new',
          name: "Test User's Knowledge",
          is_personal: true,
          owner_id: TEST_USER_ID,
          document_count: 0,
        })
      );
      await Promise.all([p1, p2]);
    });

    expect(knowledgeBaseAPI.ensurePersonal).toHaveBeenCalledTimes(1);
  });

  it('records a per-file error keyed by clientKey and clears it on retry success', async () => {
    const personal = {
      id: 'kb-pk',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 0,
    };
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(personal));
    knowledgeBaseAPI.uploadDocuments
      .mockResolvedValueOnce(uploadResponse([{ filename: 'doc.pdf', success: false, error: 'Unsupported file type' }]))
      .mockResolvedValueOnce(uploadResponse([{ filename: 'doc.pdf', success: true }]));

    const { result } = renderPersonalKB();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.uploadFiles([new File(['x'], 'doc.pdf')]);
    });
    expect(result.current.errors).toHaveLength(1);
    expect(result.current.errors[0].filename).toBe('doc.pdf');
    expect(result.current.errors[0].message).toBe('Unsupported file type');
    expect(result.current.errors[0].clientKey).toBeTruthy();

    const { clientKey } = result.current.errors[0];
    await act(async () => {
      await result.current.retryFile(clientKey);
    });
    expect(result.current.errors).toEqual([]);
  });

  it('gives two same-named failed files distinct clientKeys (SHU-817 R1)', async () => {
    const personal = {
      id: 'kb-pk',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 0,
    };
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(personal));
    // Both files share the filename "dup.txt"; results come back in submission order.
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce(
      uploadResponse([
        { filename: 'dup.txt', success: false, error: 'first failed' },
        { filename: 'dup.txt', success: false, error: 'second failed' },
      ])
    );

    const { result } = renderPersonalKB();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.uploadFiles([new File(['a'], 'dup.txt'), new File(['bb'], 'dup.txt')]);
    });

    expect(result.current.errors).toHaveLength(2);
    const [e1, e2] = result.current.errors;
    expect(e1.clientKey).not.toBe(e2.clientKey);
    expect(e1.message).toBe('first failed');
    expect(e2.message).toBe('second failed');
  });

  it('handles upload response in bare-array shape', async () => {
    const personal = {
      id: 'kb-pk',
      name: "Test User's Knowledge",
      is_personal: true,
      owner_id: TEST_USER_ID,
      document_count: 0,
    };
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(personal));
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce({ data: { data: [{ filename: 'a.pdf', success: true }] } });

    const { result } = renderPersonalKB();
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
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(personal));
    knowledgeBaseAPI.uploadDocuments.mockResolvedValueOnce({ data: { data: { unexpected: 'shape' } } });

    const { result } = renderPersonalKB();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.uploadFiles([new File(['a'], 'a.pdf')]);
    });

    expect(log.warn).toHaveBeenCalledWith(
      'usePersonalKB: unexpected upload response shape',
      expect.objectContaining({ unexpected: 'shape' })
    );
  });

  it('optimistically removes a deleted document, then reconciles to the server list', async () => {
    const personal = { id: 'kb-pk', is_personal: true, owner_id: TEST_USER_ID, document_count: 2 };
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(personal));
    // Initial load returns both; the post-delete invalidation refetch returns only d2.
    knowledgeBaseAPI.getDocuments
      .mockResolvedValueOnce(
        docsResponse([
          { id: 'd1', title: 'A', processing_status: 'content_processed' },
          { id: 'd2', title: 'B', processing_status: 'content_processed' },
        ])
      )
      .mockResolvedValue(docsResponse([{ id: 'd2', title: 'B', processing_status: 'content_processed' }]));
    knowledgeBaseAPI.deleteDocument.mockResolvedValueOnce({});

    const { result } = renderPersonalKB();
    await waitFor(() => expect(result.current.docs.length).toBe(2));

    await act(async () => {
      await result.current.deleteDoc('d1');
    });

    expect(knowledgeBaseAPI.deleteDocument).toHaveBeenCalledWith('kb-pk', 'd1');
    await waitFor(() => expect(result.current.docs.map((d) => d.id)).toEqual(['d2']));
  });

  it('rolls back the optimistic removal when delete fails', async () => {
    const personal = { id: 'kb-pk', is_personal: true, owner_id: TEST_USER_ID, document_count: 2 };
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(personal));
    knowledgeBaseAPI.getDocuments.mockResolvedValue(
      docsResponse([
        { id: 'd1', title: 'A', processing_status: 'content_processed' },
        { id: 'd2', title: 'B', processing_status: 'content_processed' },
      ])
    );
    knowledgeBaseAPI.deleteDocument.mockRejectedValueOnce(new Error('boom'));

    const { result } = renderPersonalKB();
    await waitFor(() => expect(result.current.docs.length).toBe(2));

    await act(async () => {
      await expect(result.current.deleteDoc('d1')).rejects.toThrow('boom');
    });

    // Rollback + reconcile leaves both documents present.
    await waitFor(() => expect(result.current.docs.map((d) => d.id).sort()).toEqual(['d1', 'd2']));
  });

  it('reingestDoc returns {ok:true} on success and {ok:false, message} on failure', async () => {
    const personal = { id: 'kb-pk', is_personal: true, owner_id: TEST_USER_ID, document_count: 1 };
    knowledgeBaseAPI.getPersonal.mockResolvedValue(personalResponse(personal));
    knowledgeBaseAPI.getDocuments.mockResolvedValue(
      docsResponse([{ id: 'd1', title: 'A', processing_status: 'error' }])
    );

    const { result } = renderPersonalKB();
    await waitFor(() => expect(result.current.loading).toBe(false));

    knowledgeBaseAPI.reingestDocument.mockResolvedValueOnce({});
    let res;
    await act(async () => {
      res = await result.current.reingestDoc('d1');
    });
    expect(res).toEqual({ ok: true });

    knowledgeBaseAPI.reingestDocument.mockRejectedValueOnce({
      response: { data: { error: { message: 'still processing' } } },
    });
    await act(async () => {
      res = await result.current.reingestDoc('d1');
    });
    expect(res).toEqual({ ok: false, message: 'still processing' });
  });
});
