import { useRef, useCallback } from 'react';

export default function useFreshConversations() {
  const freshConversationIdsRef = useRef(new Set());

  const markFreshConversation = useCallback((conversationId) => {
    if (!conversationId) {
      return;
    }
    freshConversationIdsRef.current.add(conversationId);
  }, []);

  const clearFreshConversation = useCallback((conversationId) => {
    if (!conversationId) {
      return;
    }
    freshConversationIdsRef.current.delete(conversationId);
  }, []);

  const isConversationFresh = useCallback(
    (conversationId) => (conversationId ? freshConversationIdsRef.current.has(conversationId) : false),
    []
  );

  return {
    freshConversationIdsRef,
    markFreshConversation,
    clearFreshConversation,
    isConversationFresh,
  };
}
