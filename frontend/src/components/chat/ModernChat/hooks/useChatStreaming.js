import { useCallback, useEffect, useRef } from "react";
import { chatAPI } from "../../../../services/api";
import log from "../../../../utils/log";
import { getMessagesFromCache, rebuildCache } from "../utils/chatCache";
import { iterateSSE, tryParseJSON } from "../utils/sseParser";
import { PLACEHOLDER_THINKING } from "../utils/chatConfig";
import {
  createStreamingErrorFromResponse,
  formatStreamingError,
  ServerStreamingError,
} from "../../../../utils/streamingErrors";
import useReasoningStream from "./useReasoningStream";
import useStreamingPlaceholders from "./useStreamingPlaceholders";
import useMessageRegeneration from "./useMessageRegeneration";

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
}) => {
  const conversationRef = useRef(selectedConversation);
  const isMountedRef = useRef(true);
  const activeStreamControllersRef = useRef(new Map());

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

      const prevData = queryClient.getQueryData([
        "conversation-messages",
        conversationId,
      ]);
      const base = getMessagesFromCache(prevData);
      const updated = base.map((msg) =>
        msg.id === placeholderId
          ? { ...msg, content: streamedContentRef.current }
          : msg,
      );
      queryClient.setQueryData(
        ["conversation-messages", conversationId],
        (cache) => rebuildCache(cache || prevData, updated),
      );
      if (
        shouldAutoFollowRef?.current &&
        conversationRef.current?.id === conversationId
      ) {
        scheduleScrollToBottom?.("auto");
      }
    },
    [
      queryClient,
      scheduleScrollToBottom,
      setStreamingStarted,
      shouldAutoFollowRef,
    ],
  );

  const { appendReasoningDelta, collapseReasoningForPlaceholder } =
    useReasoningStream({
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
    startRegeneration,
    completeRegeneration,
    setVariantSelection,
    scheduleScrollToBottom,
    shouldAutoFollowRef,
    focusMessageById,
    setError,
  });

  const handleStreamingResponse = useCallback(
    async (conversationId, payload, options = {}) => {
      let abortController;
      try {
        abortController = new AbortController();
        const conversationIdKey = String(conversationId);
        if (!activeStreamControllersRef.current.has(conversationIdKey)) {
          activeStreamControllersRef.current.set(conversationIdKey, new Set());
        }
        activeStreamControllersRef.current
          .get(conversationIdKey)
          .add(abortController);
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
          options?.placeholderMap && typeof options.placeholderMap === "object"
            ? { ...options.placeholderMap }
            : null;
        const placeholderRootOption = options?.placeholderRootId || null;
        const placeholderMetaOption =
          options?.placeholderMeta &&
          typeof options.placeholderMeta === "object"
            ? { ...options.placeholderMeta }
            : {};
        const onComplete =
          typeof options?.onComplete === "function" ? options.onComplete : null;
        const placeholderLookup =
          placeholderMapOption && Object.keys(placeholderMapOption).length > 0
            ? placeholderMapOption
            : options?.tempMessageId
              ? { 0: options.tempMessageId }
              : {};
        const placeholderIdSet = new Set(
          Object.values(placeholderLookup || {}).filter(Boolean),
        );
        const streamedContentRefs = {};

        seedMetaFromCache(
          conversationId,
          placeholderLookup,
          placeholderMetaOption,
        );
        let resolvedParentId = placeholderRootOption || null;
        const tempFromCaller =
          options && options.tempMessageId ? options.tempMessageId : null;
        let tempMessageId = tempFromCaller;

        if (!tempMessageId) {
          tempMessageId = `streaming-${Date.now()}`;
          queryClient.setQueryData(
            ["conversation-messages", conversationId],
            (oldData) => {
              const existing = getMessagesFromCache(oldData);
              const streamingMessage = {
                id: tempMessageId,
                role: "assistant",
                content: PLACEHOLDER_THINKING,
                created_at: new Date().toISOString(),
                conversation_id: conversationId,
                isStreaming: true,
                isPlaceholder: true,
              };
              return rebuildCache(oldData, [...existing, streamingMessage]);
            },
          );
        }

        const ensurePlaceholderForVariant = (variantIndex) =>
          ensurePlaceholderForVariantImpl({
            conversationId,
            variantIndex,
            placeholderLookup,
            placeholderIdSet,
            placeholderMetaOption,
            streamedContentRefs,
            resolvedParentId,
            placeholderRootOption,
          });

        const syncPlaceholderParentIds = (newParentId) => {
          resolvedParentId = syncPlaceholderParentIdsImpl({
            conversationId,
            newParentId,
            resolvedParentId,
            placeholderIdSet,
          });
        };

        const assignModelInfoToPlaceholder = (
          variantIndex,
          placeholderId,
          snapshot,
        ) =>
          assignModelInfoToPlaceholderImpl(
            conversationId,
            variantIndex,
            placeholderId,
            snapshot,
            placeholderMetaOption,
          );

        Object.values(placeholderLookup).forEach((placeholderId) => {
          if (placeholderId && !streamedContentRefs[placeholderId]) {
            streamedContentRefs[placeholderId] = { current: "" };
          }
        });

        for await (const data of iterateSSE(reader)) {
          if (!isMountedRef.current) {
            break;
          }
          if (data === "[DONE]") {
            // finalize any remaining placeholder state if backend final message didn't arrive
            try {
              queryClient.setQueryData(
                ["conversation-messages", conversationId],
                (oldData) => {
                  const existing = getMessagesFromCache(oldData);
                  const updated = existing.map((m) => {
                    if (placeholderIdSet.has(m.id)) {
                      return { ...m, isStreaming: false, isPlaceholder: false };
                    }
                    return m;
                  });
                  return rebuildCache(oldData, updated);
                },
              );
            } catch (_) {}

            if (!isMountedRef.current) {
              return;
            }

            setStreamingConversationId(null);
            setStreamingStarted(false);
            if (
              shouldAutoFollowRef?.current &&
              conversationRef.current?.id === conversationId
            ) {
              scheduleScrollToBottom?.("auto");
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
            log.warn("Failed to parse streaming data", data);
            continue;
          }

          const eventType = parsed?.event;

          if (eventType === "user_message" && parsed?.content) {
            const finalUser = parsed.content;
            const tempId = parsed.client_temp_id || null;
            queryClient.setQueryData(
              ["conversation-messages", conversationId],
              (oldData) => {
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
                    if (m?.role === "user" && m?.isPlaceholder) {
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
              },
            );
            continue;
          }

          if (eventType === "final_message" && parsed?.content) {
            const finalMsg = parsed.content;
            const variantIndex = parsed?.variant_index ?? 0;
            const placeholderId = ensurePlaceholderForVariant(variantIndex);
            const key = String(
              typeof variantIndex === "number" ? variantIndex : 0,
            );
            const serverParentId = finalMsg?.parent_message_id;
            if (serverParentId) {
              syncPlaceholderParentIds(serverParentId);
            } else if (resolvedParentId) {
              finalMsg.parent_message_id = resolvedParentId;
            }
            if (typeof variantIndex === "number") {
              finalMsg.variant_index = variantIndex;
            }
            const meta = placeholderMetaOption[key];
            if (meta?.created_at) {
              finalMsg.created_at = meta.created_at;
            }
            queryClient.setQueryData(
              ["conversation-messages", conversationId],
              (oldData) => {
                const existing = getMessagesFromCache(oldData);
                const placeholderMsg = existing.find(
                  (m) => m.id === placeholderId,
                );
                const reasoningProps = placeholderMsg
                  ? {
                      reasoning_stream: placeholderMsg.reasoning_stream,
                      reasoning_collapsed:
                        placeholderMsg.reasoning_collapsed ??
                        (placeholderMsg.reasoning_stream ? true : undefined),
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
              },
            );
            placeholderIdSet.delete(placeholderId);
            delete placeholderLookup[key];
            delete placeholderMetaOption[key];
            delete streamedContentRefs[placeholderId];
            continue;
          }

          if (eventType === "error") {
            // Server sends error events with content field containing the user-friendly message
            // Also handle legacy format with error field for backward compatibility
            const errorMessage =
              parsed?.content ||
              parsed?.error ||
              "An error occurred while processing your request.";
            throw new ServerStreamingError(errorMessage);
          }

          if (eventType === "content_delta") {
            const variantIndex = parsed?.variant_index ?? 0;
            const placeholderId = ensurePlaceholderForVariant(variantIndex);
            const chunkSnapshot = (() => {
              if (
                parsed?.model_configuration &&
                typeof parsed.model_configuration === "object"
              ) {
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
              assignModelInfoToPlaceholder(
                variantIndex,
                placeholderId,
                chunkSnapshot,
              );
            }
            const chunkText =
              typeof parsed.text === "string"
                ? parsed.text
                : typeof parsed.content === "string"
                  ? parsed.content
                  : "";
            if (!chunkText) {
              continue;
            }

            processChunk(
              streamedContentRefs[placeholderId],
              conversationId,
              placeholderId,
              chunkText,
            );
            collapseReasoningForPlaceholder(conversationId, placeholderId);
            continue;
          }

          if (eventType === "reasoning_delta") {
            const variantIndex = parsed?.variant_index ?? 0;
            const placeholderId = ensurePlaceholderForVariant(variantIndex);
            const chunkSnapshot = (() => {
              if (
                parsed?.model_configuration &&
                typeof parsed.model_configuration === "object"
              ) {
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
              assignModelInfoToPlaceholder(
                variantIndex,
                placeholderId,
                chunkSnapshot,
              );
            }
            const delta =
              typeof parsed.text === "string"
                ? parsed.text
                : typeof parsed.content === "string"
                  ? parsed.content
                  : "";
            if (delta) {
              appendReasoningDelta(conversationId, placeholderId, delta);
            }
            continue;
          }

          const legacyContent =
            typeof parsed === "string"
              ? parsed
              : typeof parsed?.content === "string"
                ? parsed.content
                : "";
          if (!legacyContent) {
            continue;
          }
          const placeholderId = ensurePlaceholderForVariant(0);
          processChunk(
            streamedContentRefs[placeholderId],
            conversationId,
            placeholderId,
            legacyContent,
          );
        }
      } catch (error) {
        if (
          error?.name === "AbortError" ||
          error?.content === "The user aborted a request."
        ) {
          if (!isMountedRef.current) {
            return;
          }

          // Treat abort as a graceful completion: clear streaming state and stop placeholders.
          setStreamingConversationId(null);
          setStreamingStarted(false);

          try {
            queryClient.setQueryData(
              ["conversation-messages", conversationId],
              (oldData) => {
                const existing = getMessagesFromCache(oldData);
                const updated = existing.map((m) =>
                  m?.isStreaming ? { ...m, isStreaming: false } : m,
                );
                return rebuildCache(oldData, updated);
              },
            );
          } catch (_) {
            // best-effort cleanup only
          }

          return;
        }
        log.error("Streaming error:", error);
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

        setStreamingConversationId(null);
        setStreamingStarted(false);

        // Replace streaming placeholders with error message content
        // This shows the error in the chat bubble instead of "Thinking..."
        try {
          queryClient.setQueryData(
            ["conversation-messages", conversationId],
            (oldData) => {
              const existing = getMessagesFromCache(oldData);
              const updated = existing.map((m) => {
                // Replace assistant placeholders with error message
                if (
                  (m.isStreaming || m.isPlaceholder) &&
                  m.role === "assistant"
                ) {
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
            },
          );
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
          const controllersForConversation =
            activeStreamControllersRef.current.get(conversationIdKey);
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
    ],
  );

  return {
    handleStreamingResponse,
    handleRegenerate,
  };
};

export default useChatStreaming;
