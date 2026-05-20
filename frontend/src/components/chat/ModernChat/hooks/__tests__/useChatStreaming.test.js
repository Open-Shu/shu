/**
 * SHU-803 Vitest coverage for useChatStreaming — the slice this ticket
 * adds (stream_start parsing + stream_id stamping + handleStopStream).
 *
 * The hook depends on a query client, an axios chat API, a slew of
 * sub-hooks, and an SSE reader. Rather than mock every dependency end-to-
 * end (which would test the mocks instead of the code), this suite
 * targets the handleStopStream callback in isolation against a real
 * QueryClient. The stream_start parsing inside the SSE iteration is
 * covered by the SHU-803 backend integration suite end-to-end; here we
 * pin the Stop-button callback semantics that AC4/AC5 specify.
 */

import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { vi } from 'vitest';
import useChatStreaming from '../useChatStreaming';
import { chatAPI } from '../../../../../services/api';

vi.mock('../../../../../services/api', () => ({
  chatAPI: {
    terminateStream: vi.fn(),
  },
  // chatCache.js imports this — without it, getMessagesFromCache
  // throws and every setQueryData inside the hook explodes.
  extractDataFromResponse: (response) => {
    if (response && typeof response === 'object' && 'data' in response) {
      const firstData = response.data;
      if (firstData && typeof firstData === 'object' && 'data' in firstData) {
        return firstData.data;
      }
      return firstData;
    }
    return response;
  },
}));

// useChatStreaming pulls in a few sub-hooks that themselves call out to
// the query client. The stop-stream callback path doesn't exercise them,
// but their imports need to resolve. Stub the ones with heavy setup.
vi.mock('../useReasoningStream', () => ({
  default: () => ({
    appendReasoningDelta: vi.fn(),
    collapseReasoningForPlaceholder: vi.fn(),
  }),
}));

vi.mock('../useStreamingPlaceholders', () => ({
  default: () => ({
    assignModelInfoToPlaceholder: vi.fn(),
    seedMetaFromCache: vi.fn(),
    ensurePlaceholderForVariant: vi.fn(),
    syncPlaceholderParentIds: vi.fn(),
  }),
}));

vi.mock('../useMessageRegeneration', () => ({
  default: () => ({ handleRegenerate: vi.fn() }),
}));

// log.error is called on the unexpected-error branch — silence it so
// failing-by-design tests don't spam the test output.
vi.mock('../../../../../utils/log', () => ({
  default: {
    error: vi.fn(),
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  },
}));

const CONVERSATION_ID = 'conv-shu-803';
const STREAM_ID = 'stream-abc-123';

// Extract the messages array from the React Query cache regardless of
// whether the cache currently holds the plain array (test seed shape)
// or the double-nested envelope ``{data:{data:[...]}}`` shape that
// ``rebuildCache`` produces after any setQueryData write inside the hook.
function getMessagesFromQueryCache(queryClient, conversationId) {
  const raw = queryClient.getQueryData(['conversation-messages', conversationId]);
  if (Array.isArray(raw)) {
    return raw;
  }
  if (raw && typeof raw === 'object' && raw.data) {
    const inner = raw.data;
    if (inner && typeof inner === 'object' && Array.isArray(inner.data)) {
      return inner.data;
    }
    if (Array.isArray(inner)) {
      return inner;
    }
  }
  return [];
}

function seedCacheWithStreamingPlaceholder(queryClient, overrides = {}) {
  queryClient.setQueryData(
    ['conversation-messages', CONVERSATION_ID],
    [
      {
        id: 'placeholder-1',
        role: 'assistant',
        content: 'Thinking…',
        conversation_id: CONVERSATION_ID,
        isStreaming: true,
        isPlaceholder: true,
        streamId: STREAM_ID,
        message_metadata: {},
        ...overrides,
      },
    ]
  );
}

function makeWrapper(queryClient) {
  const Wrapper = ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  Wrapper.displayName = 'TestQueryClientWrapper';
  return Wrapper;
}

function renderUseChatStreaming(queryClient, hookOverrides = {}) {
  const setError = vi.fn();
  const result = renderHook(
    () =>
      useChatStreaming({
        queryClient,
        setError,
        setStreamingConversationId: vi.fn(),
        setStreamingStarted: vi.fn(),
        inputRef: { current: null },
        selectedConversation: { id: CONVERSATION_ID },
        setVariantSelection: vi.fn(),
        startRegeneration: vi.fn(),
        completeRegeneration: vi.fn(),
        ragRewriteMode: 'no_rag',
        scheduleScrollToBottom: vi.fn(),
        shouldAutoFollowRef: { current: false },
        focusMessageById: vi.fn(),
        replaceSideBySideParent: vi.fn(),
        selectedKBIds: [],
        ...hookOverrides,
      }),
    { wrapper: makeWrapper(queryClient) }
  );
  return { ...result, setError };
}

describe('useChatStreaming.handleStopStream (SHU-803 AC4/AC5)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('AC4: POSTs the terminate endpoint with the message streamId', async () => {
    chatAPI.terminateStream.mockResolvedValueOnce({ status: 202 });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCacheWithStreamingPlaceholder(queryClient);
    const { result } = renderUseChatStreaming(queryClient);

    await act(async () => {
      await result.current.handleStopStream({
        id: 'placeholder-1',
        conversation_id: CONVERSATION_ID,
        streamId: STREAM_ID,
        isStreaming: true,
        isPlaceholder: true,
      });
    });

    expect(chatAPI.terminateStream).toHaveBeenCalledTimes(1);
    expect(chatAPI.terminateStream).toHaveBeenCalledWith(STREAM_ID);
  });

  it('AC5: 202 optimistically flips placeholder out of isStreaming and stamps user_terminated', async () => {
    chatAPI.terminateStream.mockResolvedValueOnce({ status: 202 });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCacheWithStreamingPlaceholder(queryClient);
    const { result } = renderUseChatStreaming(queryClient);

    await act(async () => {
      await result.current.handleStopStream({
        id: 'placeholder-1',
        conversation_id: CONVERSATION_ID,
        streamId: STREAM_ID,
        isStreaming: true,
        isPlaceholder: true,
      });
    });

    const cache = getMessagesFromQueryCache(queryClient, CONVERSATION_ID);
    expect(cache).toHaveLength(1);
    expect(cache[0].isStreaming).toBe(false);
    expect(cache[0].isPlaceholder).toBe(false);
    expect(cache[0].message_metadata.stream_state).toBe('user_terminated');
  });

  it('AC5: 410 STREAM_NOT_ACTIVE is treated as success (same optimistic update)', async () => {
    // axios convention: 4xx throws; we treat 410 specifically as
    // "stream already finalized" which is success-equivalent for
    // the user-facing flow.
    chatAPI.terminateStream.mockRejectedValueOnce({ response: { status: 410 } });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCacheWithStreamingPlaceholder(queryClient);
    const { result, setError } = renderUseChatStreaming(queryClient);

    await act(async () => {
      await result.current.handleStopStream({
        id: 'placeholder-1',
        conversation_id: CONVERSATION_ID,
        streamId: STREAM_ID,
        isStreaming: true,
        isPlaceholder: true,
      });
    });

    const cache = getMessagesFromQueryCache(queryClient, CONVERSATION_ID);
    expect(cache[0].isStreaming).toBe(false);
    expect(cache[0].message_metadata.stream_state).toBe('user_terminated');
    // 410 must NOT surface an error toast.
    expect(setError).not.toHaveBeenCalled();
  });

  it('AC5: 403 surfaces an error toast and leaves the placeholder unchanged', async () => {
    chatAPI.terminateStream.mockRejectedValueOnce({ response: { status: 403 } });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCacheWithStreamingPlaceholder(queryClient);
    const { result, setError } = renderUseChatStreaming(queryClient);

    await act(async () => {
      await result.current.handleStopStream({
        id: 'placeholder-1',
        conversation_id: CONVERSATION_ID,
        streamId: STREAM_ID,
        isStreaming: true,
        isPlaceholder: true,
      });
    });

    const cache = getMessagesFromQueryCache(queryClient, CONVERSATION_ID);
    expect(cache[0].isStreaming).toBe(true);
    expect(cache[0].isPlaceholder).toBe(true);
    expect(cache[0].message_metadata.stream_state).toBeUndefined();
    expect(setError).toHaveBeenCalledTimes(1);
    expect(setError.mock.calls[0][0]).toMatch(/don't own/i);
  });

  it('AC5: 5xx / network errors surface a generic error toast', async () => {
    chatAPI.terminateStream.mockRejectedValueOnce({ response: { status: 500 } });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCacheWithStreamingPlaceholder(queryClient);
    const { result, setError } = renderUseChatStreaming(queryClient);

    await act(async () => {
      await result.current.handleStopStream({
        id: 'placeholder-1',
        conversation_id: CONVERSATION_ID,
        streamId: STREAM_ID,
        isStreaming: true,
        isPlaceholder: true,
      });
    });

    expect(setError).toHaveBeenCalledTimes(1);
    expect(setError.mock.calls[0][0]).toMatch(/couldn't stop/i);
    const cache = getMessagesFromQueryCache(queryClient, CONVERSATION_ID);
    expect(cache[0].isStreaming).toBe(true);
  });

  it('no-op (no POST) when called without a streamId', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCacheWithStreamingPlaceholder(queryClient, { streamId: undefined });
    const { result } = renderUseChatStreaming(queryClient);

    await act(async () => {
      await result.current.handleStopStream({
        id: 'placeholder-1',
        conversation_id: CONVERSATION_ID,
        isStreaming: true,
      });
    });

    expect(chatAPI.terminateStream).not.toHaveBeenCalled();
  });

  it('M2: ensemble — every placeholder sharing the streamId is marked stopped', async () => {
    // Single terminate POST cascades the user_terminated stamp across
    // every placeholder with the same streamId. Matches the backend
    // priority-based signal which terminates all variants together.
    chatAPI.terminateStream.mockResolvedValueOnce({ status: 202 });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(
      ['conversation-messages', CONVERSATION_ID],
      [
        {
          id: 'placeholder-variant-0',
          role: 'assistant',
          conversation_id: CONVERSATION_ID,
          isStreaming: true,
          isPlaceholder: true,
          streamId: STREAM_ID,
          variant_index: 0,
          message_metadata: {},
        },
        {
          id: 'placeholder-variant-1',
          role: 'assistant',
          conversation_id: CONVERSATION_ID,
          isStreaming: true,
          isPlaceholder: true,
          streamId: STREAM_ID,
          variant_index: 1,
          message_metadata: {},
        },
      ]
    );
    const { result } = renderUseChatStreaming(queryClient);

    await act(async () => {
      await result.current.handleStopStream({
        id: 'placeholder-variant-0',
        conversation_id: CONVERSATION_ID,
        streamId: STREAM_ID,
        isStreaming: true,
      });
    });

    const cache = getMessagesFromQueryCache(queryClient, CONVERSATION_ID);
    expect(cache).toHaveLength(2);
    for (const variant of cache) {
      expect(variant.isStreaming).toBe(false);
      expect(variant.isPlaceholder).toBe(false);
      expect(variant.message_metadata.stream_state).toBe('user_terminated');
    }
  });
});
