import { useCallback } from "react";
import { getMessagesFromCache, rebuildCache } from "../utils/chatCache";

const useReasoningStream = ({ queryClient }) => {
  const appendReasoningDelta = useCallback(
    (conversationId, placeholderId, delta) => {
      if (!conversationId || !placeholderId || !delta) {
        return;
      }
      queryClient.setQueryData(
        ["conversation-messages", conversationId],
        (oldData) => {
          const existing = getMessagesFromCache(oldData);
          const updated = existing.map((msg) =>
            msg.id === placeholderId
              ? {
                  ...msg,
                  reasoning_stream: `${msg.reasoning_stream || ""}${delta}`,
                  reasoning_collapsed: msg.reasoning_collapsed ?? false,
                }
              : msg,
          );
          return rebuildCache(oldData, updated);
        },
      );
    },
    [queryClient],
  );

  const collapseReasoningForPlaceholder = useCallback(
    (conversationId, placeholderId) => {
      if (!conversationId || !placeholderId) {
        return;
      }
      queryClient.setQueryData(
        ["conversation-messages", conversationId],
        (oldData) => {
          const existing = getMessagesFromCache(oldData);
          let changed = false;
          const updated = existing.map((msg) => {
            if (
              msg.id === placeholderId &&
              msg.reasoning_stream &&
              !msg.reasoning_collapsed
            ) {
              changed = true;
              return { ...msg, reasoning_collapsed: true };
            }
            return msg;
          });
          return changed ? rebuildCache(oldData, updated) : oldData;
        },
      );
    },
    [queryClient],
  );

  return {
    appendReasoningDelta,
    collapseReasoningForPlaceholder,
  };
};

export default useReasoningStream;
