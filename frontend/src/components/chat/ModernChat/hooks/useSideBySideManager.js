import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getMessagesFromCache, rebuildCache } from '../utils/chatCache';

const useSideBySideManager = ({
  variantGroups,
  streamingVariantParentIds,
  regenerationRequests,
  queryClient,
  selectedConversationId,
}) => {
  const [sideBySideMode, setSideBySideMode] = useState({});
  const autoSideBySideParentsRef = useRef(new Set());
  const pendingAutoEnableRef = useRef(null);
  const regenerationBlockRef = useRef(new Set());
  const regenerationParentMapRef = useRef(new Map());
  const regenerationRequestsRef = useRef(regenerationRequests);

  const debug = useCallback((event, payload = {}) => {
    try {
      if (localStorage.getItem('chat_debug') === 'sidebyside') {
        // eslint-disable-next-line no-console
        console.debug('[SideBySide]', event, payload);
      }
    } catch (_) {
      /* no-op */
    }
  }, []);

  useEffect(() => {
    regenerationRequestsRef.current = regenerationRequests;
  }, [regenerationRequests]);

  useEffect(() => {
    autoSideBySideParentsRef.current = new Set();
    pendingAutoEnableRef.current = null;
    regenerationBlockRef.current = new Set();
    regenerationParentMapRef.current = new Map();
    setSideBySideMode({});
  }, [selectedConversationId]);

  const regeneratingParentIds = useMemo(() => {
    const ids = new Set();
    if (regenerationRequests && typeof regenerationRequests.forEach === 'function') {
      regenerationRequests.forEach((entry) => {
        if (entry?.parentId && entry?.status === 'pending') {
          ids.add(entry.parentId);
        }
      });
    }
    return ids;
  }, [regenerationRequests]);

  const enqueueAutoEnable = useCallback((conversationId, parentId) => {
    if (!conversationId || !parentId) {
      return;
    }
    pendingAutoEnableRef.current = { conversationId, parentId };
  }, []);

  useEffect(() => {
    setSideBySideMode((prev) => {
      const next = { ...prev };
      let updated = false;
      const groups = variantGroups || {};
      const validParents = new Set(Object.keys(groups));
      const autoParents = autoSideBySideParentsRef.current;

      Object.keys(next).forEach((parentId) => {
        if (!validParents.has(parentId)) {
          if (autoParents.has(parentId)) {
            autoParents.delete(parentId);
          }
          delete next[parentId];
          updated = true;
          debug('removed_parent', { parentId });
        }
      });

      const streamingParents = streamingVariantParentIds || new Set();
      streamingParents.forEach((parentId) => {
        if (
          !validParents.has(parentId) ||
          regeneratingParentIds.has(parentId) ||
          regenerationBlockRef.current.has(parentId)
        ) {
          debug('skip_auto_enable', {
            parentId,
            reason: !validParents.has(parentId)
              ? 'invalid_parent'
              : regeneratingParentIds.has(parentId)
              ? 'regenerating'
              : 'regen_block',
          });
          return;
        }
        const variantsForParent = groups[parentId];
        if (!Array.isArray(variantsForParent) || variantsForParent.length <= 1) {
          debug('skip_auto_enable', { parentId, reason: 'single_variant' });
          return;
        }
        if (!next[parentId]) {
          next[parentId] = true;
          autoParents.add(parentId);
          updated = true;
          debug('auto_enable_stream', { parentId });
        }
      });

      return updated ? next : prev;
    });
  }, [variantGroups, streamingVariantParentIds, regeneratingParentIds, debug]);

  useEffect(() => {
    const pending = pendingAutoEnableRef.current;
    if (!pending || pending.conversationId !== selectedConversationId) {
      return;
    }
    const parentId = pending.parentId;
    const group = variantGroups?.[parentId];
    if (!Array.isArray(group) || group.length <= 1) {
      debug('pending_auto_skipped', {
        parentId,
        groupSize: Array.isArray(group) ? group.length : 0,
      });
      return;
    }
    pendingAutoEnableRef.current = null;
    setSideBySideMode((prev) => {
      if (prev[parentId]) {
        return prev;
      }
      debug('pending_auto_enable', { parentId });
      return { ...prev, [parentId]: true };
    });
  }, [variantGroups, selectedConversationId, debug]);

  const toggleSideBySide = useCallback(
    (parentId) => {
      if (!parentId) return;
      autoSideBySideParentsRef.current.delete(parentId);
      setSideBySideMode((prev) => {
        const nextValue = !prev[parentId];
        debug('manual_toggle', { parentId, value: nextValue });
        return { ...prev, [parentId]: nextValue };
      });
    },
    [debug]
  );

  const collapseSideBySideParent = useCallback(
    (parentId) => {
      if (!parentId) return;
      setSideBySideMode((prev) => {
        if (!prev || prev[parentId] === false) {
          return prev;
        }
        debug('collapse_parent', { parentId });
        return { ...prev, [parentId]: false };
      });
    },
    [debug]
  );

  const replaceSideBySideParent = useCallback(
    (oldId, newId) => {
      if (!oldId || !newId || oldId === newId) {
        return;
      }
      setSideBySideMode((prev) => {
        if (!prev || !(oldId in prev) || newId in prev) {
          return prev;
        }
        const value = prev[oldId];
        const next = { ...prev };
        delete next[oldId];
        next[newId] = value;
        debug('replace_parent', { oldId, newId, value });
        return next;
      });
    },
    [debug]
  );

  const registerRegenerationStart = useCallback(
    (messageId, parentId) => {
      if (!parentId) {
        return;
      }
      regenerationBlockRef.current.add(parentId);
      regenerationParentMapRef.current.set(messageId, parentId);
      autoSideBySideParentsRef.current.delete(parentId);
      debug('regen_block_start', { parentId });
    },
    [debug]
  );

  const registerRegenerationComplete = useCallback(
    (messageId) => {
      const parentId = regenerationParentMapRef.current.get(messageId);
      regenerationParentMapRef.current.delete(messageId);
      if (!parentId) {
        return;
      }
      const requests = regenerationRequestsRef.current;
      const stillPending =
        requests &&
        typeof requests.forEach === 'function' &&
        Array.from(requests.values()).some(
          (entry) => entry?.parentId === parentId && entry?.status === 'pending'
        );
      if (!stillPending) {
        regenerationBlockRef.current.delete(parentId);
        debug('regen_block_end', { parentId });
      }
    },
    [debug]
  );

  const handleToggleReasoning = useCallback(
    (messageId, collapsed) => {
      if (!selectedConversationId) {
        return;
      }
      queryClient.setQueryData(['conversation-messages', selectedConversationId], (oldData) => {
        const existing = getMessagesFromCache(oldData);
        const updated = existing.map((msg) =>
          msg.id === messageId ? { ...msg, reasoning_collapsed: collapsed } : msg
        );
        return rebuildCache(oldData, updated);
      });
    },
    [queryClient, selectedConversationId]
  );

  const sideBySideParents = useMemo(
    () =>
      new Set(
        Object.entries(sideBySideMode)
          .filter(([, enabled]) => Boolean(enabled))
          .map(([parentId]) => parentId)
      ),
    [sideBySideMode]
  );

  return {
    sideBySideParents,
    toggleSideBySide,
    collapseSideBySideParent,
    replaceSideBySideParent,
    registerRegenerationStart,
    registerRegenerationComplete,
    enqueueAutoEnable,
    handleToggleReasoning,
  };
};

export default useSideBySideManager;
