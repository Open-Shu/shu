import { useCallback, useEffect, useRef } from 'react';
import { chatAPI } from '../../../../services/api';
import log from '../../../../utils/log';
import { getMessagesFromCache, rebuildCache } from '../utils/chatCache';
import { iterateSSE, tryParseJSON } from '../utils/sseParser';
import { PLACEHOLDER_THINKING } from '../utils/chatConfig';
import {
  createStreamingErrorFromResponse,
  formatStreamingError,
  ServerStreamingError,
} from '../../../../utils/streamingErrors';
import useReasoningStream from './useReasoningStream';
import useStreamingPlaceholders from './useStreamingPlaceholders';
import useMessageRegeneration from './useMessageRegeneration';

// SHU-803 AC5: response-status codes the Stop POST distinguishes.
// 202 = signal accepted (axios resolves 2xx). 410 = STREAM_NOT_ACTIVE —
// the stream already finalized before the POST landed; treated as
// success-equivalent for the UI. 403 = caller doesn't own the stream
// (defensive; shouldn't happen for the streaming user).
const STREAM_NOT_ACTIVE_STATUS = 410;
const FORBIDDEN_STATUS = 403;

// SHU-803 follow-up: per-stream-instance token used to identify which
// invocation of `handleStreamingResponse` / `handleRegenerate` currently
// owns the global streaming state. See `streamingOwnerRef` for the full
// rationale. `crypto.randomUUID()` is the preferred source (RFC 4122 v4
// from the browser's CSPRNG); the fallback handles older environments
// like vitest's jsdom in some configurations.
const makeStreamToken = () =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

export { makeStreamToken };

const useChatStreaming = ({
  queryClient,
  setError,
  setStreamingConversationId,
  setStreamingStarted,
  inputRef,
  selectedConversation,
  setVariantSelection,
  startRegeneration,
  completeRegeneration,
  ragRewriteMode,
  scheduleScrollToBottom,
  shouldAutoFollowRef,
  focusMessageById,
  replaceSideBySideParent,
  selectedKBIds,
}) => {
  const conversationRef = useRef(selectedConversation);
  const isMountedRef = useRef(true);
  const activeStreamControllersRef = useRef(new Map());
  // SHU-803 AC1: stream_id captured from the first `stream_start` SSE
  // event. Keyed by conversationId so a multi-tab user with multiple
  // simultaneous streams can stop each independently. Map values reset
  // when a stream completes or the conversation unmounts.
  const streamIdByConversationRef = useRef(new Map());
  // SHU-803 follow-up: which stream-instance token currently owns the
  // global streaming state (`streamingConversationId` +
  // `streamingStarted`). Each call to `handleStreamingResponse` /
  // `handleRegenerate` generates a fresh `streamToken` and stores it
  // here; every clear callsite checks the ref against its OWN closure-
  // captured token before acting.
  //
  // Keyed by **stream-instance token (not conversationId)** because a
  // conversation-id key falsely matches the same-conversation
  // follow-up case: user stops stream A, then starts stream B in the
  // same conversation while A drains; A's later [DONE] would see
  // `ref === A.conversationId === B.conversationId` and clear B's
  // state. The token is per-call-instance, so A's closure-captured
  // token does NOT match B's owner-claim, and A's [DONE] correctly
  // no-ops.
  const streamingOwnerRef = useRef(null);

  useEffect(() => {
    conversationRef.current = selectedConversation;
  }, [selectedConversation]);

  useEffect(() => {
    // In React 18 StrictMode, effects mount, clean up, and re-mount once.
    // Always reset isMountedRef to true on mount so streaming logic can run
    // in dev as well as production.
    isMountedRef.current = true;

    const controllersRef = activeStreamControllersRef.current;

    return () => {
      isMountedRef.current = false;
      controllersRef.forEach((controllers) => {
        controllers.forEach((controller) => {
          try {
            controller.abort();
          } catch {
            // no-op
          }
        });
      });
      controllersRef.clear();
    };
  }, []);

  const processChunk = useCallback(
    (streamedContentRef, conversationId, placeholderId, content) => {
      if (!placeholderId || !isMountedRef.current) {
        return;
      }
      streamedContentRef.current += content;
      setStreamingStarted(true);

      const prevData = queryClient.getQueryData(['conversation-messages', conversationId]);
      const base = getMessagesFromCache(prevData);
      const updated = base.map((msg) =>
        msg.id === placeholderId ? { ...msg, content: streamedContentRef.current } : msg
      );
      queryClient.setQueryData(['conversation-messages', conversationId], (cache) =>
        rebuildCache(cache || prevData, updated)
      );
      if (shouldAutoFollowRef?.current && conversationRef.current?.id === conversationId) {
        scheduleScrollToBottom?.('auto');
      }
    },
    [queryClient, scheduleScrollToBottom, setStreamingStarted, shouldAutoFollowRef]
  );

  const { appendReasoningDelta, collapseReasoningForPlaceholder } = useReasoningStream({
    queryClient,
  });

  const {
    assignModelInfoToPlaceholder: assignModelInfoToPlaceholderImpl,
    seedMetaFromCache,
    ensurePlaceholderForVariant: ensurePlaceholderForVariantImpl,
    syncPlaceholderParentIds: syncPlaceholderParentIdsImpl,
  } = useStreamingPlaceholders({
    queryClient,
    replaceSideBySideParent,
  });

  const { handleRegenerate } = useMessageRegeneration({
    queryClient,
    conversationRef,
    ragRewriteMode,
    selectedKBIds,
    startRegeneration,
    completeRegeneration,
    setVariantSelection,
    scheduleScrollToBottom,
    shouldAutoFollowRef,
    focusMessageById,
    setError,
    setStreamingConversationId,
    setStreamingStarted,
    streamingOwnerRef,
  });

  const handleStreamingResponse = useCallback(
    async (conversationId, payload, options = {}) => {
      let abortController;
      // SHU-803 follow-up: per-stream-instance token captured in this
      // closure. All clear paths below check `streamingOwnerRef.current
      // === streamToken` — that's the per-stream check that survives
      // the same-conversation race (stop A → start B in same conv;
      // A's later [DONE] sees its own captured token, ref holds B's,
      // no clear). See the `streamingOwnerRef` declaration for full
      // context.
      const streamToken = makeStreamToken();
      // SHU-803 follow-up: this stream's server-assigned streamId once
      // `stream_start` lands. Captured here so we only delete from
      // `streamIdByConversationRef` if the map still holds OUR id —
      // a newer same-conv stream might have overwritten the entry.
      let capturedStreamId = null;
      try {
        abortController = new AbortController();
        const conversationIdKey = String(conversationId);
        if (!activeStreamControllersRef.current.has(conversationIdKey)) {
          activeStreamControllersRef.current.set(conversationIdKey, new Set());
        }
        activeStreamControllersRef.current.get(conversationIdKey).add(abortController);
        // SHU-803 follow-up: claim ownership of the global streaming
        // state BEFORE setting it. Each clear path below checks the
        // ref against this stream's captured `streamToken` so a
        // straggling [DONE] / error / abort from an old (stopped or
        // navigated-away) stream cannot blow away a newer stream's
        // state — even if the newer stream is in the same conversation.
        streamingOwnerRef.current = streamToken;
        setStreamingConversationId(conversationId);
        setStreamingStarted(false);

        const response = await chatAPI.streamMessage(conversationId, payload, {
          signal: abortController.signal,
        });
        if (!response.ok) {
          // Create structured error with user-friendly message
          throw await createStreamingErrorFromResponse(response);
        }

        const reader = response.body.getReader();
        const placeholderMapOption =
          options?.placeholderMap && typeof options.placeholderMap === 'object' ? { ...options.placeholderMap } : null;
        const placeholderRootOption = options?.placeholderRootId || null;
        const placeholderMetaOption =
          options?.placeholderMeta && typeof options.placeholderMeta === 'object' ? { ...options.placeholderMeta } : {};
        const onComplete = typeof options?.onComplete === 'function' ? options.onComplete : null;
        const placeholderLookup =
          placeholderMapOption && Object.keys(placeholderMapOption).length > 0
            ? placeholderMapOption
            : options?.tempMessageId
              ? { 0: options.tempMessageId }
              : {};
        const placeholderIdSet = new Set(Object.values(placeholderLookup || {}).filter(Boolean));
        const streamedContentRefs = {};

        seedMetaFromCache(conversationId, placeholderLookup, placeholderMetaOption);
        let resolvedParentId = placeholderRootOption || null;
        const tempFromCaller = options && options.tempMessageId ? options.tempMessageId : null;
        let tempMessageId = tempFromCaller;

        if (!tempMessageId) {
          tempMessageId = `streaming-${Date.now()}`;
          queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
            const existing = getMessagesFromCache(oldData);
            const streamingMessage = {
              id: tempMessageId,
              role: 'assistant',
              content: PLACEHOLDER_THINKING,
              created_at: new Date().toISOString(),
              conversation_id: conversationId,
              isStreaming: true,
              isPlaceholder: true,
              // SHU-803 follow-up: stamp this stream's token so
              // handleStopStream can read it from the cache message
              // and check its ownership-ref guard correctly. Carries
              // through the full placeholder lifecycle.
              streamToken,
            };
            return rebuildCache(oldData, [...existing, streamingMessage]);
          });
        }

        const ensurePlaceholderForVariant = (variantIndex) => {
          const placeholderId = ensurePlaceholderForVariantImpl({
            conversationId,
            variantIndex,
            placeholderLookup,
            placeholderIdSet,
            placeholderMetaOption,
            streamedContentRefs,
            resolvedParentId,
            placeholderRootOption,
          });
          // SHU-803: stamp streamId on this placeholder if stream_start
          // has already landed. Ensemble variants share one stream_id;
          // each new variant placeholder needs the id stamped so its
          // Stop button has the value to POST.
          // SHU-803 follow-up: ALSO stamp this stream's per-instance
          // streamToken so handleStopStream's ownership guard can
          // match it against the ref. streamToken is always known
          // here (set at handleStreamingResponse entry).
          const streamId = streamIdByConversationRef.current.get(String(conversationId));
          if (placeholderId) {
            queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
              const existing = getMessagesFromCache(oldData);
              let mutated = false;
              const updated = existing.map((m) => {
                if (m.id !== placeholderId) {
                  return m;
                }
                const updates = {};
                if (streamId && m.streamId !== streamId) {
                  updates.streamId = streamId;
                }
                if (m.streamToken !== streamToken) {
                  updates.streamToken = streamToken;
                }
                if (Object.keys(updates).length === 0) {
                  return m;
                }
                mutated = true;
                return { ...m, ...updates };
              });
              return mutated ? rebuildCache(oldData, updated) : oldData;
            });
          }
          return placeholderId;
        };

        const syncPlaceholderParentIds = (newParentId) => {
          resolvedParentId = syncPlaceholderParentIdsImpl({
            conversationId,
            newParentId,
            resolvedParentId,
            placeholderIdSet,
          });
        };

        const assignModelInfoToPlaceholder = (variantIndex, placeholderId, snapshot) =>
          assignModelInfoToPlaceholderImpl(
            conversationId,
            variantIndex,
            placeholderId,
            snapshot,
            placeholderMetaOption
          );

        Object.values(placeholderLookup).forEach((placeholderId) => {
          if (placeholderId && !streamedContentRefs[placeholderId]) {
            streamedContentRefs[placeholderId] = { current: '' };
          }
        });

        for await (const data of iterateSSE(reader)) {
          if (!isMountedRef.current) {
            break;
          }
          if (data === '[DONE]') {
            // finalize any remaining placeholder state if backend final message didn't arrive
            try {
              queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
                const existing = getMessagesFromCache(oldData);
                const updated = existing.map((m) => {
                  if (placeholderIdSet.has(m.id)) {
                    return { ...m, isStreaming: false, isPlaceholder: false };
                  }
                  return m;
                });
                return rebuildCache(oldData, updated);
              });
            } catch (_) {
              // Ignore error
            }
            // SHU-803: stream completed — clear OUR captured stream_id
            // from the per-conversation map so a future stream gets a
            // fresh capture from its own stream_start event. Guarded
            // because the entry may have been overwritten by a newer
            // same-conversation stream; deleting blindly would orphan
            // that newer stream's Stop-button lookup.
            if (
              capturedStreamId !== null &&
              streamIdByConversationRef.current.get(String(conversationId)) === capturedStreamId
            ) {
              streamIdByConversationRef.current.delete(String(conversationId));
            }

            if (!isMountedRef.current) {
              return;
            }

            // SHU-803 follow-up: only clear the global streaming state
            // if THIS stream still owns it. Keyed by per-stream-instance
            // token (not conversationId) so a newer same-conv stream's
            // claim isn't matched by this stream's closure-captured
            // token.
            if (streamingOwnerRef.current === streamToken) {
              streamingOwnerRef.current = null;
              setStreamingConversationId(null);
              setStreamingStarted(false);
            }
            if (shouldAutoFollowRef?.current && conversationRef.current?.id === conversationId) {
              scheduleScrollToBottom?.('auto');
            }
            setTimeout(() => {
              if (!isMountedRef.current) {
                return;
              }
              inputRef.current?.focus();
            }, 100);
            onComplete?.();
            return;
          }

          const parsed = tryParseJSON(data);
          if (!parsed) {
            log.warn('Failed to parse streaming data', data);
            continue;
          }

          const eventType = parsed?.event;

          // SHU-803 AC1: capture the stream_id from the first SSE event
          // so the Stop button has the id it needs to POST the terminate
          // request. The event is additive — pre-SHU-803 clients ignore
          // it. Stamping the streamId on every current placeholder lets
          // MessageItem read it directly from the message in cache
          // (rather than threading another ref through the render tree).
          if (eventType === 'stream_start') {
            const streamId = parsed?.content?.stream_id;
            if (streamId) {
              // Capture our streamId so the [DONE] / abort / error
              // cleanup below can verify the map entry still belongs to
              // us before deleting (a newer same-conv stream may have
              // overwritten the slot in the interim).
              capturedStreamId = streamId;
              streamIdByConversationRef.current.set(String(conversationId), streamId);
              queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
                const existing = getMessagesFromCache(oldData);
                const updated = existing.map((m) => (placeholderIdSet.has(m.id) ? { ...m, streamId, streamToken } : m));
                return rebuildCache(oldData, updated);
              });
            }
            continue;
          }

          if (eventType === 'user_message' && parsed?.content) {
            const finalUser = parsed.content;
            const tempId = parsed.client_temp_id || null;
            queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
              const existing = getMessagesFromCache(oldData);
              if (!Array.isArray(existing)) {
                return oldData;
              }

              let targetIndex = -1;
              if (tempId) {
                targetIndex = existing.findIndex((m) => m.id === tempId);
              }
              if (targetIndex === -1) {
                // Fallback: replace the latest user placeholder
                for (let i = existing.length - 1; i >= 0; i--) {
                  const m = existing[i];
                  if (m?.role === 'user' && m?.isPlaceholder) {
                    targetIndex = i;
                    break;
                  }
                }
              }
              if (targetIndex >= 0) {
                const updated = [...existing];
                updated[targetIndex] = finalUser;
                return rebuildCache(oldData, updated);
              }
              // If no placeholder found, append conservatively
              return rebuildCache(oldData, [...existing, finalUser]);
            });
            continue;
          }

          if (eventType === 'final_message' && parsed?.content) {
            const finalMsg = parsed.content;
            const variantIndex = parsed?.variant_index ?? 0;
            const placeholderId = ensurePlaceholderForVariant(variantIndex);
            const key = String(typeof variantIndex === 'number' ? variantIndex : 0);
            const serverParentId = finalMsg?.parent_message_id;
            if (serverParentId) {
              syncPlaceholderParentIds(serverParentId);
            } else if (resolvedParentId) {
              finalMsg.parent_message_id = resolvedParentId;
            }
            if (typeof variantIndex === 'number') {
              finalMsg.variant_index = variantIndex;
            }
            const meta = placeholderMetaOption[key];
            if (meta?.created_at) {
              finalMsg.created_at = meta.created_at;
            }
            queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
              const existing = getMessagesFromCache(oldData);
              const placeholderMsg = existing.find((m) => m.id === placeholderId);
              const reasoningProps = placeholderMsg
                ? {
                    reasoning_stream: placeholderMsg.reasoning_stream,
                    reasoning_collapsed:
                      placeholderMsg.reasoning_collapsed ?? (placeholderMsg.reasoning_stream ? true : undefined),
                  }
                : {};
              const updated = existing.map((m) => {
                if (m.id === placeholderId) {
                  return {
                    ...finalMsg,
                    ...reasoningProps,
                    isPlaceholder: false,
                    isStreaming: false,
                  };
                }
                return m;
              });
              const replaced = updated.some((m) => m.id === finalMsg.id);
              if (!replaced) {
                // Placeholder missing; append to preserve presence
                updated.push({
                  ...finalMsg,
                  ...reasoningProps,
                  isPlaceholder: false,
                  isStreaming: false,
                });
              }
              return rebuildCache(oldData, updated);
            });
            placeholderIdSet.delete(placeholderId);
            delete placeholderLookup[key];
            delete placeholderMetaOption[key];
            delete streamedContentRefs[placeholderId];
            continue;
          }

          if (eventType === 'error') {
            // Server sends error events with content field containing the user-friendly message
            // Also handle legacy format with error field for backward compatibility
            const errorMessage = parsed?.content || parsed?.error || 'An error occurred while processing your request.';
            throw new ServerStreamingError(errorMessage);
          }

          if (eventType === 'content_delta') {
            const variantIndex = parsed?.variant_index ?? 0;
            const placeholderId = ensurePlaceholderForVariant(variantIndex);
            const chunkSnapshot = (() => {
              if (parsed?.model_configuration && typeof parsed.model_configuration === 'object') {
                const snapshot = { ...parsed.model_configuration };
                if (!snapshot.model_name && parsed?.model_name) {
                  snapshot.model_name = parsed.model_name;
                }
                if (!snapshot.display_name && parsed?.model_display_name) {
                  snapshot.display_name = parsed.model_display_name;
                }
                return snapshot;
              }
              if (parsed?.model_name || parsed?.model_display_name) {
                return {
                  model_name: parsed.model_name || parsed.model_display_name,
                  display_name: parsed.model_display_name,
                };
              }
              return null;
            })();
            if (chunkSnapshot) {
              assignModelInfoToPlaceholder(variantIndex, placeholderId, chunkSnapshot);
            }
            const chunkText =
              typeof parsed.text === 'string' ? parsed.text : typeof parsed.content === 'string' ? parsed.content : '';
            if (!chunkText) {
              continue;
            }

            processChunk(streamedContentRefs[placeholderId], conversationId, placeholderId, chunkText);
            collapseReasoningForPlaceholder(conversationId, placeholderId);
            continue;
          }

          if (eventType === 'reasoning_delta') {
            const variantIndex = parsed?.variant_index ?? 0;
            const placeholderId = ensurePlaceholderForVariant(variantIndex);
            const chunkSnapshot = (() => {
              if (parsed?.model_configuration && typeof parsed.model_configuration === 'object') {
                const snapshot = { ...parsed.model_configuration };
                if (!snapshot.model_name && parsed?.model_name) {
                  snapshot.model_name = parsed.model_name;
                }
                if (!snapshot.display_name && parsed?.model_display_name) {
                  snapshot.display_name = parsed.model_display_name;
                }
                return snapshot;
              }
              if (parsed?.model_name || parsed?.model_display_name) {
                return {
                  model_name: parsed.model_name || parsed.model_display_name,
                  display_name: parsed.model_display_name,
                };
              }
              return null;
            })();
            if (chunkSnapshot) {
              assignModelInfoToPlaceholder(variantIndex, placeholderId, chunkSnapshot);
            }
            const delta =
              typeof parsed.text === 'string' ? parsed.text : typeof parsed.content === 'string' ? parsed.content : '';
            if (delta) {
              appendReasoningDelta(conversationId, placeholderId, delta);
            }
            continue;
          }

          const legacyContent =
            typeof parsed === 'string' ? parsed : typeof parsed?.content === 'string' ? parsed.content : '';
          if (!legacyContent) {
            continue;
          }
          const placeholderId = ensurePlaceholderForVariant(0);
          processChunk(streamedContentRefs[placeholderId], conversationId, placeholderId, legacyContent);
        }
      } catch (error) {
        if (error?.name === 'AbortError' || error?.content === 'The user aborted a request.') {
          if (!isMountedRef.current) {
            return;
          }

          // Treat abort as a graceful completion: clear streaming state and stop placeholders.
          // SHU-803 follow-up: ownership-guard the clear, keyed by this
          // stream's per-instance token — see the [DONE] handler above
          // for full rationale.
          if (streamingOwnerRef.current === streamToken) {
            streamingOwnerRef.current = null;
            setStreamingConversationId(null);
            setStreamingStarted(false);
          }

          try {
            queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
              const existing = getMessagesFromCache(oldData);
              const updated = existing.map((m) => (m?.isStreaming ? { ...m, isStreaming: false } : m));
              return rebuildCache(oldData, updated);
            });
          } catch (_) {
            // best-effort cleanup only
          }

          return;
        }
        log.error('Streaming error:', error);
        if (!isMountedRef.current) {
          return;
        }

        // Format error with user-friendly message
        const errorInfo = formatStreamingError(error);
        const displayMessage = errorInfo.retryAfter
          ? `${errorInfo.message} (retry in ${errorInfo.retryAfter}s)`
          : errorInfo.message;

        // Create a friendly error message for the chat bubble based on error type
        let chatErrorMessage;
        if (errorInfo.isNetworkError) {
          chatErrorMessage =
            "I'm sorry, I encountered a network error when communicating with the server. Please check your connection and try again.";
        } else if (errorInfo.isServerError) {
          // Server-sent error - already has user-friendly message from backend
          chatErrorMessage = `I apologize, but I encountered an error: ${displayMessage}`;
        } else {
          // HTTP errors or unknown errors
          chatErrorMessage = `I'm sorry, I encountered an error: ${displayMessage}`;
        }

        // SHU-803 follow-up: ownership-guard the clear, keyed by this
        // stream's per-instance token — see the [DONE] handler above
        // for full rationale.
        if (streamingOwnerRef.current === streamToken) {
          streamingOwnerRef.current = null;
          setStreamingConversationId(null);
          setStreamingStarted(false);
        }

        // Replace streaming placeholders with error message content
        // This shows the error in the chat bubble instead of "Thinking..."
        try {
          queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
            const existing = getMessagesFromCache(oldData);
            const updated = existing.map((m) => {
              // Replace assistant placeholders with error message
              if ((m.isStreaming || m.isPlaceholder) && m.role === 'assistant') {
                return {
                  ...m,
                  content: chatErrorMessage,
                  isStreaming: false,
                  isPlaceholder: false,
                  isError: true,
                };
              }
              return m;
            });
            return rebuildCache(oldData, updated);
          });
        } catch (_) {
          // best-effort cleanup only
        }

        // Also show in the error banner for visibility
        setError(displayMessage);

        setTimeout(() => {
          if (!isMountedRef.current) {
            return;
          }
          inputRef.current?.focus();
        }, 100);
      } finally {
        if (abortController) {
          const conversationIdKey = String(conversationId);
          const controllersForConversation = activeStreamControllersRef.current.get(conversationIdKey);
          if (controllersForConversation) {
            controllersForConversation.delete(abortController);
            if (controllersForConversation.size === 0) {
              activeStreamControllersRef.current.delete(conversationIdKey);
            }
          }
        }
      }
    },
    [
      inputRef,
      queryClient,
      setError,
      setStreamingConversationId,
      setStreamingStarted,
      scheduleScrollToBottom,
      shouldAutoFollowRef,
      processChunk,
      isMountedRef,
      activeStreamControllersRef,
      appendReasoningDelta,
      assignModelInfoToPlaceholderImpl,
      collapseReasoningForPlaceholder,
      ensurePlaceholderForVariantImpl,
      seedMetaFromCache,
      syncPlaceholderParentIdsImpl,
    ]
  );

  // SHU-803 AC4/AC5: Stop-button handler. POSTs the terminate endpoint
  // and (on 202 / 410) immediately flips the placeholder out of
  // `isStreaming` with a client-side `stream_state="user_terminated"`
  // stamp so the user sees "Stopped by user" without waiting for the
  // backend's `final_message` to arrive (drain may take many seconds
  // on end-only-usage providers). When `final_message` lands, the
  // persisted Message replaces the placeholder and stream_state will
  // already match — see SHU-803 plan Decisions Log.
  //
  // Accepts a message object (so we can read streamId + conversation
  // context off it). On 403, surfaces an error toast. On 5xx / network,
  // same. The SSE iteration continues consuming the channel — we do NOT
  // abort the AbortController; that's reserved for unmount / error.
  const handleStopStream = useCallback(
    async (message) => {
      const streamId = message?.streamId;
      const conversationId = message?.conversation_id;
      if (!streamId) {
        return;
      }

      let success = false;
      try {
        await chatAPI.terminateStream(streamId);
        success = true;
      } catch (error) {
        const status = error?.response?.status;
        if (status === STREAM_NOT_ACTIVE_STATUS) {
          // STREAM_NOT_ACTIVE — the stream already finalized before
          // the POST landed. AC5 treats this as success.
          success = true;
        } else if (status === FORBIDDEN_STATUS) {
          setError("You don't own this stream — can't stop it.");
        } else {
          log.error('Failed to stop streaming response', error);
          setError("Couldn't stop the response. Please try again.");
        }
      }

      if (!success || !conversationId) {
        return;
      }

      // Optimistic update: flip EVERY placeholder sharing this stream_id
      // out of isStreaming and stamp `stream_state="user_terminated"`.
      // Ensembles share a single stream_id across all variants, so a
      // single Stop click marks all variants as stopped together.
      //
      // SHU-803 follow-up: clear ``PLACEHOLDER_THINKING`` when a Stop
      // lands before any ``content_delta`` arrived. Otherwise the
      // bubble shows "Thinking…" alongside the "Stopped by user"
      // caption for the entire backend drain window (up to ~90s) —
      // a contradictory state. Real partial content (anything other
      // than the thinking placeholder) is preserved so the user can
      // still see what they got before clicking Stop. When the
      // backend's final_message eventually lands, the persisted
      // partial content (which may itself be empty if Stop fired
      // pre-delta) replaces whatever's here.
      queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
        const existing = getMessagesFromCache(oldData);
        let mutated = false;
        const updated = existing.map((m) => {
          if (m.streamId === streamId && (m.isStreaming || m.isPlaceholder)) {
            mutated = true;
            const updatedMessage = {
              ...m,
              isStreaming: false,
              isPlaceholder: false,
              message_metadata: {
                ...(m.message_metadata || {}),
                stream_state: 'user_terminated',
              },
            };
            if (m.content === PLACEHOLDER_THINKING) {
              updatedMessage.content = '';
            }
            return updatedMessage;
          }
          return m;
        });
        return mutated ? rebuildCache(oldData, updated) : oldData;
      });

      // SHU-803 follow-up (Bug 1): release the InputBar back to Send
      // immediately. Without this, the input bar would keep showing Stop
      // until the SSE channel emits `[DONE]`, which only fires after the
      // backend drain finishes — up to ~90s on OpenRouter. The SSE
      // channel keeps consuming in the background so the persisted
      // `final_message` still lands when drain completes (the existing
      // [DONE] handler is a no-op for streamingConversationId since we
      // already cleared it here). The user can type and send a new
      // message right away; the backend persisted the partial Message
      // at signal time so chronological ordering is preserved.
      //
      // Ownership-guard the clear keyed by the stream's per-instance
      // streamToken (stamped on the placeholder at
      // handleStreamingResponse entry). A conversationId-keyed guard
      // here would falsely match a newer same-conversation stream
      // started between when the user's click captured the cached
      // message and when the terminate POST resolved. The per-stream
      // token survives that race.
      const streamToken = message?.streamToken;
      if (streamToken && streamingOwnerRef.current === streamToken) {
        streamingOwnerRef.current = null;
        setStreamingConversationId(null);
        setStreamingStarted(false);
      }
    },
    [queryClient, setError, setStreamingConversationId, setStreamingStarted]
  );

  return {
    handleStreamingResponse,
    handleRegenerate,
    handleStopStream,
  };
};

export default useChatStreaming;
