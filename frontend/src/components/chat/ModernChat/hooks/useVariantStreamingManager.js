import { useState, useMemo, useCallback, useEffect, useRef } from 'react';

import {
  buildMessageVariants,
  buildStreamingParentIds,
} from '../utils/messageVariants';

const useVariantStreamingManager = ({ messages, selectedConversationId }) => {
  const [variantSelection, setVariantSelection] = useState({});
  const [regenerationRequests, setRegenerationRequests] = useState(() => new Map());
  const previousConversationIdRef = useRef(selectedConversationId);

  useEffect(() => {
    if (previousConversationIdRef.current !== selectedConversationId) {
      setVariantSelection({});
      setRegenerationRequests(new Map());
      previousConversationIdRef.current = selectedConversationId;
    }
  }, [selectedConversationId]);

  const startRegeneration = useCallback((messageId, parentId, tempId) => {
    setRegenerationRequests((prev) => {
      const next = new Map(prev);
      next.set(messageId, {
        parentId,
        tempId,
        status: 'pending',
      });
      return next;
    });
  }, []);

  const completeRegeneration = useCallback((messageId) => {
    setRegenerationRequests((prev) => {
      if (!prev.has(messageId)) {
        return prev;
      }
      const next = new Map(prev);
      next.delete(messageId);
      return next;
    });
  }, []);

  const { visibleMessages: flattenedMessages, variantGroups } = useMemo(
    () => buildMessageVariants(messages, variantSelection),
    [messages, variantSelection]
  );

  const streamingVariantParentIds = useMemo(
    () => buildStreamingParentIds(messages),
    [messages]
  );

  const isVariantGroupStreaming = useCallback(
    (parentId) => streamingVariantParentIds.has(parentId),
    [streamingVariantParentIds]
  );

  const handleVariantChange = useCallback((parentId, index) => {
    setVariantSelection((prev) => ({ ...prev, [parentId]: index }));
  }, []);

  return {
    flattenedMessages,
    variantGroups,
    variantSelection,
    setVariantSelection,
    handleVariantChange,
    regenerationRequests,
    startRegeneration,
    completeRegeneration,
    isVariantGroupStreaming,
    streamingVariantParentIds,
  };
};

export default useVariantStreamingManager;
