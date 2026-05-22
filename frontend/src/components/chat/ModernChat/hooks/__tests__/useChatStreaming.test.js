/**
 * SHU-803 Vitest coverage for useChatStreaming — the slice this ticket
 * adds (stream_start parsing + stream_id stamping + handleStopStream).
 *
 * The hook depends on a query client, an axios chat API, a slew of
 * sub-hooks, and an SSE reader. Two surfaces are exercised here:
 *
 *   - **handleStopStream callback semantics (AC4/AC5)**: pinned in
 *     isolation against a real QueryClient with chatAPI.terminateStream
 *     mocked.
 *   - **stream_start SSE parsing (AC1)**: exercised end-to-end through
 *     `handleStreamingResponse` with a controllable mock SSE reader.
 *     Pre-seeds the cache with a placeholder, feeds a stream_start
 *     event, asserts the streamId gets stamped on the placeholder so
 *     the InputBar's Stop-button lookup
 *     (``flattenedMessages.find(m => m.isStreaming && m.streamId)``)
 *     picks it up.
 *
 * The backend integration suite proves the server-side SSE event
 * shape; this file proves the React client consumes it correctly —
 * the bar AC11 specifically calls out.
 */

import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { vi } from 'vitest';
import useChatStreaming from '../useChatStreaming';
import { chatAPI } from '../../../../../services/api';

vi.mock('../../../../../services/api', () => ({
  chatAPI: {
    terminateStream: vi.fn(),
    streamMessage: vi.fn(),
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

  it('does not clear global streaming state when this hook never owned it', async () => {
    // SHU-803 follow-up (Codex review): handleStopStream's optimistic
    // global-state clear (setStreamingConversationId(null) +
    // setStreamingStarted(false)) is ownership-guarded via the
    // hook-internal streamingOwnerRef. In this test, we never call
    // handleStreamingResponse first — so the ref stays null and a
    // direct handleStopStream call must NOT touch the global setters,
    // even though the terminate POST itself succeeded and the cache
    // optimistic update fires.
    //
    // Real-world failure this guards against: user stops stream A,
    // immediately starts stream B (which takes ownership and sets
    // streamingConversationId=B). A straggling stale handleStopStream
    // for A (or A's [DONE] / abort / error event) would unconditionally
    // null out B's state and the InputBar would briefly flip to Send
    // while B is still streaming.
    chatAPI.terminateStream.mockResolvedValueOnce({ status: 202 });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCacheWithStreamingPlaceholder(queryClient);

    const setStreamingConversationId = vi.fn();
    const setStreamingStarted = vi.fn();
    const { result } = renderUseChatStreaming(queryClient, {
      setStreamingConversationId,
      setStreamingStarted,
    });

    await act(async () => {
      await result.current.handleStopStream({
        id: 'placeholder-1',
        conversation_id: CONVERSATION_ID,
        streamId: STREAM_ID,
        isStreaming: true,
        isPlaceholder: true,
      });
    });

    // Terminate fired AND the cache optimistic update applied —
    // the guard only protects the global state setters, not the
    // user-visible "Stopped by user" stamp on the placeholder.
    expect(chatAPI.terminateStream).toHaveBeenCalledWith(STREAM_ID);
    const cache = getMessagesFromQueryCache(queryClient, CONVERSATION_ID);
    expect(cache[0].message_metadata.stream_state).toBe('user_terminated');

    // Critical assertion: the global setters were NEVER called with
    // the clear values. Since the hook never claimed ownership of the
    // global state in this test (no handleStreamingResponse), the
    // guard correctly skipped the clear path.
    expect(setStreamingConversationId).not.toHaveBeenCalledWith(null);
    expect(setStreamingStarted).not.toHaveBeenCalledWith(false);
  });

  it('clears "Thinking…" content when Stop fires before any content_delta arrived', async () => {
    // SHU-803 follow-up: pre-fix, a Stop click before the first
    // content_delta left the bubble showing "Thinking…" alongside the
    // "Stopped by user" caption for the entire backend drain window
    // (up to ~90s on OpenRouter). The two messages contradict each
    // other — the model isn't thinking, it was stopped. Fix wipes the
    // PLACEHOLDER_THINKING content in the optimistic update so the
    // bubble shows just the caption + timestamp, then the backend's
    // final_message replaces it with whatever partial content was
    // captured (often empty for a pre-delta stop).
    chatAPI.terminateStream.mockResolvedValueOnce({ status: 202 });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCacheWithStreamingPlaceholder(queryClient);
    // Confirm the seeded placeholder uses the load-bearing content
    // value — guards against a future refactor that changes the
    // placeholder copy and silently invalidates the test.
    expect(getMessagesFromQueryCache(queryClient, CONVERSATION_ID)[0].content).toBe('Thinking…');
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
    expect(cache[0].content).toBe('');
    expect(cache[0].message_metadata.stream_state).toBe('user_terminated');
  });

  it('preserves real partial content when Stop fires after content_delta arrived', async () => {
    // Symmetric guard: the "clear PLACEHOLDER_THINKING" fix MUST NOT
    // wipe real partial content. If the user clicked Stop after a
    // chunk of "Once upon a time…" streamed in, that content is the
    // most useful artifact of the interrupted exchange and must
    // survive the optimistic flip.
    chatAPI.terminateStream.mockResolvedValueOnce({ status: 202 });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCacheWithStreamingPlaceholder(queryClient, { content: 'Once upon a time…' });
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
    expect(cache[0].content).toBe('Once upon a time…');
    expect(cache[0].message_metadata.stream_state).toBe('user_terminated');
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

/**
 * Build a ReadableStream-like reader that yields one chunk containing
 * all provided SSE events, then EOF. Matches the chunk shape
 * ``iterateSSE`` expects: ``{ done, value: Uint8Array }``.
 *
 * Pass either:
 *   - a string ``"[DONE]"`` → emits ``data: [DONE]\n\n``
 *   - an object → JSON-stringified, emitted as ``data: <json>\n\n``
 */
function makeMockSseReader(events) {
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

function makeMockSseResponse(events) {
  return {
    ok: true,
    body: { getReader: () => makeMockSseReader(events) },
  };
}

/**
 * Controllable async reader that lets the test push SSE events on
 * demand and end the stream manually. Mirrors the WHATWG ReadableStream
 * reader interface (`.read() -> Promise<{ done, value }>`) so it plugs
 * into the production ``iterateSSE(reader)`` without changes.
 *
 * Use case: the SHU-803 same-conversation race test needs stream A to
 * sit mid-flight while stream B starts and completes. With a one-shot
 * "yields all events then EOF" mock that's impossible. This reader
 * exposes ``push(event)`` / ``end()`` so the test orchestrates the
 * two streams' interleaving deterministically.
 */
class ControlledSseReader {
  constructor() {
    this._chunks = [];
    this._waiters = [];
    this._encoder = new TextEncoder();
  }

  push(event) {
    const text = `data: ${typeof event === 'string' ? event : JSON.stringify(event)}\n\n`;
    this._chunks.push({ done: false, value: this._encoder.encode(text) });
    this._drain();
  }

  end() {
    this._chunks.push({ done: true, value: undefined });
    this._drain();
  }

  _drain() {
    while (this._chunks.length > 0 && this._waiters.length > 0) {
      const chunk = this._chunks.shift();
      const resolve = this._waiters.shift();
      resolve(chunk);
    }
  }

  read() {
    if (this._chunks.length > 0) {
      return Promise.resolve(this._chunks.shift());
    }
    return new Promise((resolve) => {
      this._waiters.push(resolve);
    });
  }
}

describe('useChatStreaming.handleStreamingResponse (SHU-803 AC1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('AC1: parses stream_start SSE event and stamps streamId on the placeholder', async () => {
    // Production code path under test: useChatStreaming.js line ~302 —
    // ``if (eventType === 'stream_start') { ... streamIdByConversationRef
    // .set(...) ... setQueryData stamps streamId on placeholderIdSet
    // members ... }``. The backend integration suite proves the server
    // emits the event; this test proves the React client captures it
    // and writes the streamId where the InputBar can find it.
    //
    // Without this stamp, ``activeStreamingMessage`` in ModernChat.js
    // never matches and the Stop button stays disabled / unclickable.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    // Pre-seed the cache with a placeholder. We pass options.tempMessageId
    // matching this id so the hook's placeholderLookup / placeholderIdSet
    // includes it — that's the set the stream_start branch stamps over.
    const TEMP_PLACEHOLDER_ID = 'temp-streaming-msg-1';
    queryClient.setQueryData(
      ['conversation-messages', CONVERSATION_ID],
      [
        {
          id: TEMP_PLACEHOLDER_ID,
          role: 'assistant',
          content: 'Thinking…',
          conversation_id: CONVERSATION_ID,
          isStreaming: true,
          isPlaceholder: true,
          message_metadata: {},
        },
      ]
    );

    // Mock the SSE response: stream_start with the id we want stamped,
    // then [DONE] so the hook exits cleanly. Skipping content_delta /
    // final_message here because they'd require the mocked sub-hooks
    // to behave realistically and we're testing the stream_start
    // branch in isolation.
    chatAPI.streamMessage.mockResolvedValueOnce(
      makeMockSseResponse([{ event: 'stream_start', content: { stream_id: STREAM_ID } }, '[DONE]'])
    );

    const { result } = renderUseChatStreaming(queryClient);

    await act(async () => {
      await result.current.handleStreamingResponse(
        CONVERSATION_ID,
        { message: 'hi' },
        { tempMessageId: TEMP_PLACEHOLDER_ID }
      );
    });

    const cache = getMessagesFromQueryCache(queryClient, CONVERSATION_ID);
    const placeholder = cache.find((m) => m.id === TEMP_PLACEHOLDER_ID);
    expect(placeholder).toBeDefined();
    expect(placeholder.streamId).toBe(STREAM_ID);
  });

  it('AC1: stream_start with no stream_id leaves placeholder un-stamped', async () => {
    // Defensive: if the event payload is malformed (missing
    // content.stream_id), the parser must not write anything — a row
    // with ``streamId: undefined`` would NOT match the InputBar's
    // ``m.streamId`` truthiness check anyway, but writing the field
    // pollutes the cache shape unnecessarily.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const TEMP_PLACEHOLDER_ID = 'temp-streaming-msg-2';
    queryClient.setQueryData(
      ['conversation-messages', CONVERSATION_ID],
      [
        {
          id: TEMP_PLACEHOLDER_ID,
          role: 'assistant',
          content: 'Thinking…',
          conversation_id: CONVERSATION_ID,
          isStreaming: true,
          isPlaceholder: true,
          message_metadata: {},
        },
      ]
    );

    chatAPI.streamMessage.mockResolvedValueOnce(
      makeMockSseResponse([{ event: 'stream_start', content: {} }, '[DONE]'])
    );

    const { result } = renderUseChatStreaming(queryClient);

    await act(async () => {
      await result.current.handleStreamingResponse(
        CONVERSATION_ID,
        { message: 'hi' },
        { tempMessageId: TEMP_PLACEHOLDER_ID }
      );
    });

    const cache = getMessagesFromQueryCache(queryClient, CONVERSATION_ID);
    const placeholder = cache.find((m) => m.id === TEMP_PLACEHOLDER_ID);
    expect(placeholder).toBeDefined();
    expect(placeholder.streamId).toBeUndefined();
  });

  it("same-conv race: old stream A's [DONE] does NOT clear newer stream B's state while B is still streaming", async () => {
    // Codex-flagged scenario (SHU-803 follow-up). Pre-fix the ownership
    // guard was keyed by conversationId — fine for cross-conversation
    // races, but unable to distinguish two streams in the SAME
    // conversation. The bug:
    //
    //   1. User starts stream A in conversation X → ref = X.
    //   2. User clicks Stop on A → handleStopStream clears ref.
    //   3. User sends a new message (stream B) in conversation X → ref = X.
    //   4. A's drain finishes; backend emits A's [DONE] on the old SSE.
    //   5. A's [DONE] checks ``ref === A.conversationId`` → TRUE
    //      (because B claims the SAME conv) → blindly clears state.
    //   6. B's InputBar momentarily flashes Send mid-stream.
    //
    // Post-fix the ref holds a per-stream `streamToken` generated at
    // handleStreamingResponse entry. A's closure captures token_A; B
    // overwrites ref to token_B. A's [DONE] checks ``ref === token_A``
    // → FALSE → no-op. B's state survives.
    //
    // **Test interleaving (the load-bearing part):** A's stale [DONE]
    // MUST fire WHILE B is still mid-stream. If B finishes first, its
    // own [DONE] clears the ref to null, and A's later [DONE] sees
    // ``null === A.conversationId`` → FALSE anyway. That ordering
    // would pass even with the pre-fix conv-keyed guard, making the
    // test prove nothing. So this test holds B open across A's [DONE]
    // and only releases B's [DONE] after asserting no spurious clear
    // fired.
    const readerA = new ControlledSseReader();
    const readerB = new ControlledSseReader();

    chatAPI.streamMessage
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => readerA } })
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => readerB } });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(
      ['conversation-messages', CONVERSATION_ID],
      [
        {
          id: 'temp-a',
          role: 'assistant',
          content: 'Thinking…',
          conversation_id: CONVERSATION_ID,
          isStreaming: true,
          isPlaceholder: true,
          message_metadata: {},
        },
        {
          id: 'temp-b',
          role: 'assistant',
          content: 'Thinking…',
          conversation_id: CONVERSATION_ID,
          isStreaming: true,
          isPlaceholder: true,
          message_metadata: {},
        },
      ]
    );

    const setStreamingConversationId = vi.fn();
    const setStreamingStarted = vi.fn();
    const { result } = renderUseChatStreaming(queryClient, {
      setStreamingConversationId,
      setStreamingStarted,
    });

    // Start A.
    const taskA = act(() =>
      result.current.handleStreamingResponse(CONVERSATION_ID, { message: 'A' }, { tempMessageId: 'temp-a' })
    );
    await new Promise((resolve) => setTimeout(resolve, 10));

    // A's stream_start arrives — A captures stream_id, ref = token_A.
    readerA.push({ event: 'stream_start', content: { stream_id: 'stream-A' } });
    await new Promise((resolve) => setTimeout(resolve, 10));

    // Start B in the SAME conversation. B's claim overwrites the ref
    // with its own token_B.
    const taskB = act(() =>
      result.current.handleStreamingResponse(CONVERSATION_ID, { message: 'B' }, { tempMessageId: 'temp-b' })
    );
    await new Promise((resolve) => setTimeout(resolve, 10));

    // B's stream_start arrives — B is now actively streaming.
    // We deliberately do NOT push B's [DONE] yet — B must stay
    // "live" across A's stale [DONE].
    readerB.push({ event: 'stream_start', content: { stream_id: 'stream-B' } });
    await new Promise((resolve) => setTimeout(resolve, 10));

    // Baseline: zero null-clears have fired yet (both streams active,
    // neither has reached [DONE]).
    const nullCallsBaseline = setStreamingConversationId.mock.calls.filter(([arg]) => arg === null).length;
    expect(nullCallsBaseline).toBe(0);

    // A's stale [DONE] fires WHILE B is still mid-stream. This is the
    // exact race the token-based guard exists to prevent. Pre-fix,
    // A's [DONE] would check ``ref === A.conversationId`` → TRUE
    // (because B took the SAME conv) → clear B's state to null.
    // Post-fix, A's closure-captured token doesn't match B's token
    // in the ref → no-op.
    readerA.push('[DONE]');
    readerA.end();
    await taskA;

    // Critical assertion: zero null-clears even after A's stale [DONE].
    // B is still streaming; its state must be intact. If this becomes
    // 1, the conv-keyed guard regression is back.
    const nullCallsAfterA = setStreamingConversationId.mock.calls.filter(([arg]) => arg === null).length;
    expect(nullCallsAfterA).toBe(0);

    // Now release B's [DONE]. B clears its own state — this is the
    // first and only null-clear that should fire across both streams.
    readerB.push('[DONE]');
    readerB.end();
    await taskB;

    const nullCallsAfterB = setStreamingConversationId.mock.calls.filter(([arg]) => arg === null).length;
    expect(nullCallsAfterB).toBe(1);
  });
});
