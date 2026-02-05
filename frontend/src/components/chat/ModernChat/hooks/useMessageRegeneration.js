import { useCallback, useEffect, useRef } from 'react';
import { chatRegenerateAPI, extractDataFromResponse } from '../../../../services/api';
import log from '../../../../utils/log';
import { getMessagesFromCache, rebuildCache } from '../utils/chatCache';
import { iterateSSE, tryParseJSON } from '../utils/sseParser';
import { PLACEHOLDER_THINKING } from '../utils/chatConfig';

const useMessageRegeneration = ({
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
}) => {
  const isMountedRef = useRef(false);
  const abortControllerRef = useRef(null);

  useEffect(() => {
    isMountedRef.current = true;

    return () => {
      isMountedRef.current = false;
      if (abortControllerRef.current) {
        try {
          abortControllerRef.current.abort();
        } catch (_) {
          // no-op
        }
        abortControllerRef.current = null;
      }
    };
  }, []);

  const updateTempRegenContent = useCallback(
    (conversationId, tempId, newContent, extra = {}) => {
      if (!isMountedRef.current) {
        return;
      }

      queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
        const existing = getMessagesFromCache(oldData);
        const updated = [...existing];
        for (let i = updated.length - 1; i >= 0; i--) {
          if (updated[i].id === tempId) {
            updated[i] = { ...updated[i], content: newContent, ...extra };
            break;
          }
        }
        return rebuildCache(oldData, updated);
      });
    },
    [queryClient]
  );

  const handleRegenerate = useCallback(
    async (messageId, parentMessageId) => {
      const conversation = conversationRef.current;
      if (!conversation?.id) {
        return;
      }

      const conversationId = conversation.id;
      const parentId = parentMessageId || messageId;
      const tempId = `regen-temp-${Date.now()}`;

      // Cancel any in-flight regeneration request.
      if (abortControllerRef.current) {
        try {
          abortControllerRef.current.abort();
        } catch (_) {
          // no-op
        }
        abortControllerRef.current = null;
      }

      startRegeneration(messageId, parentId, tempId);

      queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
        const existing = getMessagesFromCache(oldData);
        const placeholder = {
          id: tempId,
          role: 'assistant',
          content: PLACEHOLDER_THINKING,
          created_at: new Date().toISOString(),
          conversation_id: conversationId,
          isStreaming: true,
          isPlaceholder: true,
          parent_message_id: parentId,
          suppressSideBySide: true,
          variant_index: Number.MAX_SAFE_INTEGER,
        };

        try {
          if (localStorage.getItem('chat_debug') === 'sidebyside') {
            // eslint-disable-next-line no-console
            console.debug('[SideBySide] regen_placeholder_created', {
              parentId,
              placeholderId: tempId,
              parentMessageId: parentId,
            });
          }
        } catch (err) {
          /* no-op */
        }

        let insertIndex = -1;
        for (let i = existing.length - 1; i >= 0; i--) {
          const candidate = existing[i];
          if (
            candidate.role === 'assistant' &&
            ((candidate.parent_message_id && candidate.parent_message_id === parentId) || candidate.id === parentId)
          ) {
            insertIndex = i;
            break;
          }
        }

        if (insertIndex >= 0) {
          const head = existing.slice(0, insertIndex + 1);
          const tail = existing.slice(insertIndex + 1);
          return rebuildCache(oldData, [...head, placeholder, ...tail]);
        }

        return rebuildCache(oldData, [...existing, placeholder]);
      });

      setVariantSelection((prev) => ({
        ...prev,
        [parentId]: Number.MAX_SAFE_INTEGER,
      }));

      if (shouldAutoFollowRef?.current) {
        scheduleScrollToBottom?.('auto');
      }

      setTimeout(() => {
        if (!isMountedRef.current) {
          return;
        }

        const data = queryClient.getQueryData(['conversation-messages', conversationId]);
        const msgs = extractDataFromResponse(data) || [];
        const group = msgs
          .filter(
            (m) =>
              m.role === 'assistant' && ((m.parent_message_id && m.parent_message_id === parentId) || m.id === parentId)
          )
          .sort((a, b) => new Date(a.created_at) - new Date(b.created_at));

        if (group.length > 0) {
          const latestVariantIndex = group.length - 1;
          const latestVariantId = group[latestVariantIndex]?.id ?? tempId;
          if (latestVariantId) {
            focusMessageById?.(latestVariantId);
          }
        }
        setVariantSelection((prev) => ({
          ...prev,
          [parentId]: Number.MAX_SAFE_INTEGER,
        }));
      }, 0);

      let cleanupRan = false;

      const markCompleted = () => {
        if (cleanupRan) {
          return;
        }
        cleanupRan = true;

        if (!isMountedRef.current) {
          return;
        }

        completeRegeneration(messageId);
      };

      try {
        const abortController = new AbortController();
        abortControllerRef.current = abortController;

        const response = await chatRegenerateAPI.streamRegenerate(
          messageId,
          {
            parent_message_id: parentId,
            rag_rewrite_mode: ragRewriteMode,
          },
          { signal: abortController.signal }
        );
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('Regenerate stream: missing response body');
        }

        let regenAccum = '';
        let hasContentStarted = false;

        for await (const payload of iterateSSE(reader)) {
          if (!isMountedRef.current) {
            break;
          }

          if (payload === '[DONE]') {
            try {
              queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
                const existing = getMessagesFromCache(oldData);
                const updated = existing.map((m) =>
                  m.id === tempId ? { ...m, isStreaming: false, isPlaceholder: false } : m
                );
                return rebuildCache(oldData, updated);
              });
            } catch (_) {}

            markCompleted();
            return;
          }

          const parsed = tryParseJSON(payload);
          if (!parsed) {
            log.warn('Failed to parse SSE payload (regenerate)', payload);
            continue;
          }

          const eventType = parsed?.event;
          if (eventType === 'final_message' && parsed?.content) {
            const created = {
              ...(parsed.content || {}),
              content:
                typeof parsed.text === 'string'
                  ? parsed.text
                  : typeof parsed.content?.content === 'string'
                    ? parsed.content.content
                    : parsed.content?.content,
            };
            queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
              const existing = getMessagesFromCache(oldData);
              const withoutPlaceholders = existing.filter((m) => {
                if (!m?.isPlaceholder) {
                  return true;
                }
                return m.role !== 'assistant' || m.id === tempId;
              });
              const mergedMap = new Map();
              withoutPlaceholders.forEach((m) => {
                if (m.id !== tempId) {
                  mergedMap.set(m.id, m);
                }
              });
              mergedMap.set(created.id, created);
              const merged = Array.from(mergedMap.values());
              return rebuildCache(oldData, merged);
            });
            markCompleted();
            continue;
          }

          if (eventType === 'error') {
            throw new Error(parsed?.error || 'Streaming error');
          }

          if (eventType === 'content_delta') {
            const chunkText =
              typeof parsed.text === 'string' ? parsed.text : typeof parsed.content === 'string' ? parsed.content : '';
            if (!chunkText) {
              continue;
            }

            regenAccum += chunkText;
            const extra = {};
            if (!hasContentStarted) {
              hasContentStarted = true;
              extra.reasoning_collapsed = true;
            }
            updateTempRegenContent(conversationId, tempId, regenAccum, extra);
            continue;
          }

          if (eventType === 'reasoning_delta') {
            const delta =
              typeof parsed.text === 'string' ? parsed.text : typeof parsed.content === 'string' ? parsed.content : '';
            if (!delta) {
              continue;
            }

            queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
              const existing = getMessagesFromCache(oldData);
              const updated = existing.map((m) => {
                if (m.id !== tempId) {
                  return m;
                }
                const prev =
                  typeof m.reasoning_stream === 'string'
                    ? m.reasoning_stream
                    : Array.isArray(m.reasoning_stream)
                      ? m.reasoning_stream.join('')
                      : '';
                const stream = `${prev}${delta}`;
                return {
                  ...m,
                  reasoning_stream: stream,
                  reasoning_collapsed: m.reasoning_collapsed ?? false,
                };
              });
              return rebuildCache(oldData, updated);
            });
            continue;
          }

          const content = typeof parsed?.content === 'string' ? parsed.content : '';
          if (!content) {
            continue;
          }

          regenAccum += content;
          const extra = {};
          if (!hasContentStarted) {
            hasContentStarted = true;
            extra.reasoning_collapsed = true;
          }
          updateTempRegenContent(conversationId, tempId, regenAccum, extra);
        }

        queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
          const existing = getMessagesFromCache(oldData);
          const updated = existing
            .map((m) => (m.id === tempId ? { ...m, isStreaming: false, isPlaceholder: false } : m))
            .filter((m) => m.id !== tempId);
          return rebuildCache(oldData, updated);
        });
        markCompleted();
      } catch (error) {
        if (error?.name === 'AbortError' || error?.message === 'The user aborted a request.') {
          // Treat abort as a graceful completion.
          markCompleted();
          return;
        }

        const readable = error && error.message ? error.message : 'Unknown error';
        log.error('Regenerate failed:', error);

        if (isMountedRef.current) {
          setError(`Regenerate failed: ${readable}`);
          updateTempRegenContent(conversationId, tempId, `Regeneration failed: ${readable}`, {
            isStreaming: false,
            isPlaceholder: false,
            role: 'assistant',
            parent_message_id: parentId,
          });
        }

        markCompleted();
      } finally {
        if (abortControllerRef.current) {
          try {
            abortControllerRef.current.abort();
          } catch (_) {
            // no-op
          }
          abortControllerRef.current = null;
        }
        markCompleted();
      }
    },
    [
      completeRegeneration,
      queryClient,
      ragRewriteMode,
      setError,
      setVariantSelection,
      startRegeneration,
      updateTempRegenContent,
      scheduleScrollToBottom,
      shouldAutoFollowRef,
      focusMessageById,
      conversationRef,
    ]
  );

  return { handleRegenerate };
};

export default useMessageRegeneration;
