import { useCallback } from 'react';
import log from '../../../../utils/log';
import { getMessagesFromCache, rebuildCache } from '../utils/chatCache';
import { PLACEHOLDER_THINKING } from '../utils/chatConfig';

const useStreamingPlaceholders = ({ queryClient, replaceSideBySideParent }) => {
  const assignModelInfoToPlaceholder = useCallback(
    (conversationId, variantIndex, placeholderId, snapshot, placeholderMetaOption) => {
      if (!placeholderId || !snapshot || typeof snapshot !== 'object') {
        return;
      }

      const key = String(typeof variantIndex === 'number' ? variantIndex : 0);
      const meta = placeholderMetaOption[key] || {};
      if (meta.modelInfoAssigned) {
        return;
      }

      const normalizedSnapshot = {
        ...snapshot,
        provider: snapshot.provider ? { ...snapshot.provider } : undefined,
      };

      placeholderMetaOption[key] = {
        ...meta,
        modelInfoAssigned: true,
        model_configuration: normalizedSnapshot,
      };

      queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
        const existing = getMessagesFromCache(oldData);
        const updated = existing.map((msg) =>
          msg.id === placeholderId
            ? {
                ...msg,
                model_configuration: normalizedSnapshot,
                message_metadata: {
                  ...(msg.message_metadata || {}),
                  model_configuration: normalizedSnapshot,
                },
              }
            : msg,
        );
        return rebuildCache(oldData, updated);
      });
    },
    [queryClient],
  );

  const seedMetaFromCache = useCallback(
    (conversationId, placeholderLookup, placeholderMetaOption) => {
      try {
        const cacheData = queryClient.getQueryData(['conversation-messages', conversationId]);
        const cacheMessages = getMessagesFromCache(cacheData);
        if (!Array.isArray(cacheMessages)) {
          return;
        }

        Object.entries(placeholderLookup).forEach(([key, id]) => {
          if (!id) return;
          if (placeholderMetaOption[key]?.created_at) {
            return;
          }

          const msg = cacheMessages.find((m) => m.id === id);
          if (msg?.created_at) {
            placeholderMetaOption[key] = { id, created_at: msg.created_at };
          }
        });
      } catch (error) {
        log.warn('Failed to seed placeholder metadata from cache', error);
      }
    },
    [queryClient],
  );

  const ensurePlaceholderForVariant = useCallback(
    ({
      conversationId,
      variantIndex,
      placeholderLookup,
      placeholderIdSet,
      placeholderMetaOption,
      streamedContentRefs,
      resolvedParentId,
      placeholderRootOption,
    }) => {
      const key = String(typeof variantIndex === 'number' ? variantIndex : 0);
      let placeholderId = placeholderLookup[key];

      if (!placeholderId) {
        placeholderId = `streaming-${Date.now()}-variant-${key}`;
        placeholderLookup[key] = placeholderId;
        placeholderIdSet.add(placeholderId);

        const nowIso = new Date().toISOString();
        placeholderMetaOption[key] = { id: placeholderId, created_at: nowIso };

        queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
          const existing = getMessagesFromCache(oldData);
          const placeholder = {
            id: placeholderId,
            role: 'assistant',
            content: PLACEHOLDER_THINKING,
            created_at: nowIso,
            conversation_id: conversationId,
            isStreaming: true,
            isPlaceholder: true,
            parent_message_id: resolvedParentId || placeholderRootOption || placeholderId,
            variant_index: typeof variantIndex === 'number' ? variantIndex : undefined,
          };
          return rebuildCache(oldData, [...existing, placeholder]);
        });
      } else if (!placeholderMetaOption[key]) {
        const nowIso = new Date().toISOString();
        placeholderMetaOption[key] = {
          id: placeholderId,
          created_at: nowIso,
        };
      }

      if (!streamedContentRefs[placeholderId]) {
        streamedContentRefs[placeholderId] = { current: '' };
      }

      return placeholderId;
    },
    [queryClient],
  );

  const syncPlaceholderParentIds = useCallback(
    ({ conversationId, newParentId, resolvedParentId, placeholderIdSet }) => {
      if (!newParentId) {
        return resolvedParentId;
      }

      if (resolvedParentId && newParentId !== resolvedParentId) {
        replaceSideBySideParent?.(resolvedParentId, newParentId);
      }

      if (newParentId === resolvedParentId || placeholderIdSet.size === 0) {
        return newParentId;
      }

      queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
        const existing = getMessagesFromCache(oldData);
        const updated = existing.map((msg) =>
          placeholderIdSet.has(msg.id) ? { ...msg, parent_message_id: newParentId } : msg,
        );
        return rebuildCache(oldData, updated);
      });

      return newParentId;
    },
    [queryClient, replaceSideBySideParent],
  );

  return {
    assignModelInfoToPlaceholder,
    seedMetaFromCache,
    ensurePlaceholderForVariant,
    syncPlaceholderParentIds,
  };
};

export default useStreamingPlaceholders;

