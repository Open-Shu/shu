/**
 * SHU-803 follow-up Vitest coverage for useMessageRegeneration — the
 * Stop-during-regen wiring (`setStreamingConversationId`,
 * `setStreamingStarted`, `stream_start` → placeholder streamId stamp).
 *
 * Pre-fix, this hook ran its own SSE loop independently of
 * `handleStreamingResponse` and never touched the streaming-state
 * setters or captured the stream_id. The InputBar therefore never
 * swapped Send → Stop during regenerate, and even if it had, the
 * placeholder had no streamId for the terminate POST to target.
 *
 * These tests pin the post-fix invariants:
 *
 * - On `handleRegenerate` invocation: `setStreamingConversationId` is
 *   called with the conversation id (synchronously, before the SSE
 *   request fires) and `setStreamingStarted(false)` is called.
 * - On `stream_start` SSE event: the regen temp placeholder in cache
 *   gets `streamId` stamped on it — that's what
 *   `handleInputBarStop`'s `flattenedMessages.find(...)` lookup keys
 *   off.
 * - On first `content_delta`: `setStreamingStarted(true)` flips so
 *   the InputBar moves from "Initializing…" to enabled Stop.
 * - On `[DONE]`: both setters are cleared (back to `null` /
 *   `false`) — the InputBar returns to Send.
 */

import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { vi } from 'vitest';
import useMessageRegeneration from '../useMessageRegeneration';
import { chatRegenerateAPI } from '../../../../../services/api';

vi.mock('../../../../../services/api', () => ({
  chatRegenerateAPI: {
    streamRegenerate: vi.fn(),
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

vi.mock('../../../../../utils/log', () => ({
  default: {
    error: vi.fn(),
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  },
}));

const CONVERSATION_ID = 'conv-regen-stop';
const TARGET_MESSAGE_ID = 'target-msg-1';
const PARENT_ID = 'parent-1';
const STREAM_ID = 'stream-regen-xyz';

/**
 * Build a ReadableStream-like reader that yields one chunk containing
 * all the provided SSE events, then EOF. Matches the chunk shape
 * `iterateSSE` expects: ``{ done, value: Uint8Array }``.
 *
 * Pass either:
 *   - a string like ``"[DONE]"`` → emits ``data: [DONE]\n\n``
 *   - an object → JSON-stringified, emitted as ``data: <json>\n\n``
 */
function makeMockReader(events) {
  const encoder = new TextEncoder();
  const sseText = events.map((e) => `data: ${typeof e === 'string' ? e : JSON.stringify(e)}\n\n`).join('');
  const value = encoder.encode(sseText);
  let consumed = false;
  return {
    read: async () => {
      if (consumed) {
        return { done: true, value: undefined };
      }
      consumed = true;
      return { done: false, value };
    },
  };
}

function makeMockResponse(events) {
  return {
    ok: true,
    body: {
      getReader: () => makeMockReader(events),
    },
  };
}

function makeWrapper(queryClient) {
  const Wrapper = ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  Wrapper.displayName = 'RegenTestQueryClientWrapper';
  return Wrapper;
}

function renderUseRegeneration(queryClient, overrides = {}) {
  const setStreamingConversationId = vi.fn();
  const setStreamingStarted = vi.fn();
  const setError = vi.fn();
  const startRegeneration = vi.fn();
  const completeRegeneration = vi.fn();
  const setVariantSelection = vi.fn();
  const scheduleScrollToBottom = vi.fn();
  const focusMessageById = vi.fn();
  // SHU-803 follow-up: ownership ref. In production this is owned by
  // useChatStreaming and shared with useMessageRegeneration so both
  // hooks coordinate on the "who currently owns the global streaming
  // state" check before any clear.
  const streamingOwnerRef = { current: null };

  const result = renderHook(
    () =>
      useMessageRegeneration({
        queryClient,
        conversationRef: { current: { id: CONVERSATION_ID } },
        ragRewriteMode: 'no_rag',
        selectedKBIds: [],
        startRegeneration,
        completeRegeneration,
        setVariantSelection,
        scheduleScrollToBottom,
        shouldAutoFollowRef: { current: false },
        focusMessageById,
        setError,
        setStreamingConversationId,
        setStreamingStarted,
        streamingOwnerRef,
        ...overrides,
      }),
    { wrapper: makeWrapper(queryClient) }
  );
  return {
    ...result,
    setStreamingConversationId,
    setStreamingStarted,
    setError,
    startRegeneration,
    completeRegeneration,
    streamingOwnerRef,
  };
}

/**
 * Pull the messages array from the React Query cache regardless of
 * whether the cache currently holds the plain array or the double-
 * nested envelope ``{data:{data:[...]}}`` that `rebuildCache` writes
 * after the first `setQueryData` call.
 */
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

describe('useMessageRegeneration — SHU-803 Stop-during-regen wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('sets streamingConversationId at the start of regenerate (before SSE response)', async () => {
    // Capture the moment of dispatch — we use a never-resolving stream
    // so the test only observes the synchronous prelude (before any
    // SSE event arrives). The dispatched call to
    // `setStreamingConversationId` is what flips the InputBar from
    // Send → Stop the instant the user clicks regenerate.
    chatRegenerateAPI.streamRegenerate.mockReturnValueOnce(
      new Promise(() => {
        /* never resolves */
      })
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result, setStreamingConversationId, setStreamingStarted } = renderUseRegeneration(queryClient);

    act(() => {
      // Fire-and-forget — we don't await; we want to assert on the
      // synchronous prelude only.
      result.current.handleRegenerate(TARGET_MESSAGE_ID, PARENT_ID);
    });

    expect(setStreamingConversationId).toHaveBeenCalledWith(CONVERSATION_ID);
    expect(setStreamingStarted).toHaveBeenCalledWith(false);
  });

  it('stamps streamId on the regen placeholder when stream_start fires', async () => {
    chatRegenerateAPI.streamRegenerate.mockResolvedValueOnce(
      makeMockResponse([{ event: 'stream_start', content: { stream_id: STREAM_ID } }, '[DONE]'])
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderUseRegeneration(queryClient);

    await act(async () => {
      await result.current.handleRegenerate(TARGET_MESSAGE_ID, PARENT_ID);
    });

    const messages = getMessagesFromQueryCache(queryClient, CONVERSATION_ID);
    // The placeholder may have been cleaned up by the [DONE] path
    // depending on the cleanup branch taken; what we care about is
    // that AT SOME POINT during the iteration the streamId got
    // stamped onto a placeholder with this stream_id. Easiest
    // observable: at least one row in the final cache (or its history)
    // saw the stream_id. We capture this by checking that the
    // setQueryData call carried a row with the streamId — but since
    // we only have the final state, we observe via the placeholder
    // that's still present at [DONE]-cleanup time (it's been flipped
    // out of `isStreaming` but the streamId stamp persists).
    const placeholdersOrFinals = messages.filter(
      (m) => m && (m.streamId === STREAM_ID || (m.isPlaceholder === false && m.streamId === STREAM_ID))
    );
    expect(placeholdersOrFinals.length).toBeGreaterThan(0);
  });

  it('flips streamingStarted to true on first content_delta', async () => {
    chatRegenerateAPI.streamRegenerate.mockResolvedValueOnce(
      makeMockResponse([
        { event: 'stream_start', content: { stream_id: STREAM_ID } },
        { event: 'content_delta', text: 'Hello ' },
        { event: 'content_delta', text: 'world.' },
        '[DONE]',
      ])
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result, setStreamingStarted } = renderUseRegeneration(queryClient);

    await act(async () => {
      await result.current.handleRegenerate(TARGET_MESSAGE_ID, PARENT_ID);
    });

    // Sequence: initial `false` (handleRegenerate prelude), then
    // `true` on first content_delta, then `false` again on
    // markCompleted at [DONE]. We don't care about call count beyond
    // "true was observed at least once between false-false bracket."
    const calls = setStreamingStarted.mock.calls.map(([arg]) => arg);
    expect(calls).toContain(true);
    expect(calls).toContain(false);
  });

  it('clears streamingConversationId on [DONE]', async () => {
    chatRegenerateAPI.streamRegenerate.mockResolvedValueOnce(
      makeMockResponse([
        { event: 'stream_start', content: { stream_id: STREAM_ID } },
        { event: 'content_delta', text: 'ok' },
        '[DONE]',
      ])
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result, setStreamingConversationId, setStreamingStarted } = renderUseRegeneration(queryClient);

    await act(async () => {
      await result.current.handleRegenerate(TARGET_MESSAGE_ID, PARENT_ID);
    });

    // The final call to each setter should be the clear (null / false)
    // — that's what releases the InputBar back to Send.
    const conversationIdCalls = setStreamingConversationId.mock.calls.map(([arg]) => arg);
    const startedCalls = setStreamingStarted.mock.calls.map(([arg]) => arg);
    expect(conversationIdCalls[conversationIdCalls.length - 1]).toBeNull();
    expect(startedCalls[startedCalls.length - 1]).toBe(false);
  });

  it('clears streamingConversationId on stream error', async () => {
    chatRegenerateAPI.streamRegenerate.mockResolvedValueOnce(
      makeMockResponse([{ event: 'error', error: 'simulated provider failure' }])
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result, setStreamingConversationId, setStreamingStarted } = renderUseRegeneration(queryClient);

    await act(async () => {
      await result.current.handleRegenerate(TARGET_MESSAGE_ID, PARENT_ID);
    });

    // Error path also goes through markCompleted in the finally block,
    // so the clear must still fire — otherwise the InputBar would be
    // stuck on Stop forever after a failed regen.
    const conversationIdCalls = setStreamingConversationId.mock.calls.map(([arg]) => arg);
    expect(conversationIdCalls).toContain(null);
    const startedCalls = setStreamingStarted.mock.calls.map(([arg]) => arg);
    expect(startedCalls).toContain(false);
  });
});
