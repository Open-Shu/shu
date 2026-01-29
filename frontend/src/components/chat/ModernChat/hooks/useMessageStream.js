import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useQuery } from "react-query";

import {
  chatAPI,
  extractDataFromResponse,
  formatError,
} from "../../../../services/api";
import log from "../../../../utils/log";
import { CHAT_PAGE_SIZE } from "../utils/chatConfig";

const normalizeMessagesResponse = async (conversationId) => {
  const response = await chatAPI.getMessages(conversationId, {
    limit: CHAT_PAGE_SIZE,
    offset: 0,
    order: "desc",
    include_total: true,
  });

  const payload = extractDataFromResponse(response);
  const rawMessages = Array.isArray(payload?.messages)
    ? payload.messages
    : Array.isArray(payload)
      ? payload
      : [];
  const chatOrderMessages = [...rawMessages].reverse();
  const totalCount =
    typeof payload?.total_count === "number"
      ? payload.total_count
      : typeof response?.data?.meta?.total_count === "number"
        ? response.data.meta.total_count
        : null;

  const responseData = response?.data || {};
  const normalizedMeta = {
    ...(responseData.meta || {}),
    total_count: totalCount,
    order: "asc",
    page_size: CHAT_PAGE_SIZE,
  };

  return {
    totalCount,
    response: {
      ...response,
      data: {
        ...responseData,
        data: chatOrderMessages,
        meta: normalizedMeta,
      },
    },
  };
};

const useMessageStream = ({
  selectedConversation,
  queryClient,
  setError,
  scheduleScrollToBottom,
  setIsPinnedToBottom,
  clearFreshConversation,
  markFreshConversation,
}) => {
  const totalMessagesRef = useRef(null);
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);

  const {
    data: messagesResponse,
    isLoading: loadingMessages,
    isFetching: fetchingMessages,
  } = useQuery(
    ["conversation-messages", selectedConversation?.id],
    async () => {
      if (!selectedConversation?.id) {
        return null;
      }
      const { response, totalCount } = await normalizeMessagesResponse(
        selectedConversation.id,
      );
      totalMessagesRef.current =
        typeof totalCount === "number" ? totalCount : null;
      return response;
    },
    {
      enabled: !!selectedConversation?.id,
      keepPreviousData: true,
      onError: (err) => {
        setError(formatError(err).message);
      },
    },
  );

  const messages = useMemo(
    () =>
      selectedConversation?.id
        ? extractDataFromResponse(messagesResponse) || []
        : [],
    [messagesResponse, selectedConversation?.id],
  );

  const resetConversationState = useCallback(() => {
    setHasMoreMessages(false);
    setLoadingOlderMessages(false);
    setIsInitialLoad(true);
    totalMessagesRef.current = null;
  }, []);

  useEffect(() => {
    resetConversationState();
    if (selectedConversation?.id) {
      setIsPinnedToBottom(true);
      scheduleScrollToBottom("auto");
    }
  }, [
    selectedConversation?.id,
    resetConversationState,
    setIsPinnedToBottom,
    scheduleScrollToBottom,
  ]);

  useEffect(() => {
    if (!selectedConversation?.id || !messagesResponse) {
      return;
    }

    const totalCount =
      typeof messagesResponse?.data?.meta?.total_count === "number"
        ? messagesResponse.data.meta.total_count
        : null;
    if (typeof totalCount === "number") {
      totalMessagesRef.current = totalCount;
    }

    const currentMessages = extractDataFromResponse(messagesResponse);
    const currentArray = Array.isArray(currentMessages) ? currentMessages : [];
    const persistedCount = currentArray.filter(
      (msg) => !msg?.isPlaceholder,
    ).length;
    const moreAvailable =
      typeof totalCount === "number"
        ? persistedCount < totalCount
        : persistedCount >= CHAT_PAGE_SIZE;
    setHasMoreMessages(moreAvailable);

    if (!loadingMessages) {
      const hasSummary =
        Boolean(selectedConversation?.summary_text) ||
        Boolean(selectedConversation?.meta?.summary_last_message_id);
      if (persistedCount > 0 || hasSummary) {
        clearFreshConversation(selectedConversation.id);
      } else {
        markFreshConversation(selectedConversation.id);
      }
    }

    if (isInitialLoad && currentArray.length > 0 && !loadingMessages) {
      setIsInitialLoad(false);
      scheduleScrollToBottom("auto");
    }
  }, [
    messagesResponse,
    selectedConversation?.id,
    selectedConversation?.summary_text,
    selectedConversation?.meta?.summary_last_message_id,
    loadingMessages,
    isInitialLoad,
    clearFreshConversation,
    markFreshConversation,
    scheduleScrollToBottom,
  ]);

  const loadOlderMessages = useCallback(
    async ({ captureScrollSnapshot, restoreScrollSnapshot, expandWindow }) => {
      if (!selectedConversation?.id) {
        return;
      }
      if (!hasMoreMessages || loadingOlderMessages || fetchingMessages) {
        return;
      }

      const cacheKey = ["conversation-messages", selectedConversation.id];
      const snapshot = captureScrollSnapshot?.();
      setIsPinnedToBottom(false);

      const cached = queryClient.getQueryData(cacheKey);
      const existingMessages = extractDataFromResponse(cached);
      const existingArray = Array.isArray(existingMessages)
        ? existingMessages
        : [];
      const persistedCount = existingArray.filter(
        (msg) => !msg?.isPlaceholder,
      ).length;

      setLoadingOlderMessages(true);
      try {
        const response = await chatAPI.getMessages(selectedConversation.id, {
          limit: CHAT_PAGE_SIZE,
          offset: persistedCount,
          order: "desc",
          include_total: false,
        });

        const payload = extractDataFromResponse(response);
        const rawMessages = Array.isArray(payload?.messages)
          ? payload.messages
          : Array.isArray(payload)
            ? payload
            : [];
        const olderMessages = [...rawMessages].reverse();

        if (olderMessages.length === 0) {
          setHasMoreMessages(false);
          return;
        }

        queryClient.setQueryData(cacheKey, (oldData) => {
          if (!oldData) {
            return oldData;
          }

          const current = extractDataFromResponse(oldData);
          const currentArray = Array.isArray(current) ? current : [];
          const merged = [...olderMessages, ...currentArray];

          if (oldData && typeof oldData === "object" && "data" in oldData) {
            const outer = { ...oldData };
            const inner = { ...(outer.data || {}) };
            const meta = { ...(inner.meta || {}) };
            if (typeof meta.total_count === "number") {
              totalMessagesRef.current = meta.total_count;
            }
            inner.meta = meta;
            inner.data = merged;
            outer.data = inner;
            return outer;
          }

          return { data: { data: merged } };
        });

        const mergedCount = persistedCount + olderMessages.length;
        const totalCount = totalMessagesRef.current;
        const moreAvailable =
          typeof totalCount === "number"
            ? mergedCount < totalCount
            : olderMessages.length === CHAT_PAGE_SIZE;
        setHasMoreMessages(moreAvailable);
        expandWindow?.(olderMessages.length);
        if (snapshot) {
          requestAnimationFrame(() => {
            restoreScrollSnapshot?.(snapshot);
          });
        }
      } catch (err) {
        log.error("Failed to load older messages", err);
      } finally {
        setLoadingOlderMessages(false);
      }
    },
    [
      selectedConversation?.id,
      hasMoreMessages,
      loadingOlderMessages,
      fetchingMessages,
      queryClient,
      setIsPinnedToBottom,
    ],
  );

  return {
    messages,
    loadingMessages,
    fetchingMessages,
    hasMoreMessages,
    loadingOlderMessages,
    loadOlderMessages,
  };
};

export default useMessageStream;
