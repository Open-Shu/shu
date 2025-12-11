import { useCallback } from 'react';
import { useMutation, useQueryClient } from 'react-query';

import {
  extractDataFromResponse,
  formatError,
  sideCallsAPI,
} from '../../../../services/api';

const useConversationLifecycle = ({
  conversationQueryKey,
  clearFreshConversation,
  setSelectedConversation,
  onError,
  onSideCallNotConfigured,
}) => {
  const queryClient = useQueryClient();

  const handleError = useCallback(
    (err, fallbackMessage) => {
      const formatted = formatError(err);
      const message =
        typeof formatted === 'string'
          ? formatted
          : formatted?.message || fallbackMessage;

      // When side-caller is not configured, treat this as a soft warning that
      // is handled via header UI instead of a global error banner.
      if (
        typeof message === 'string' &&
        message.includes('No side-call model configured')
      ) {
        if (onSideCallNotConfigured) {
          onSideCallNotConfigured();
        }
        return;
      }

      if (!onError) return;
      onError(message);
    },
    [onError, onSideCallNotConfigured]
  );

  const generateSummaryMutation = useMutation(
    ({ conversationId, payload = {} }) =>
      sideCallsAPI.generateSummary(conversationId, payload),
    {
      onError: (err) => {
        handleError(err, 'Summary failed');
      },
      onSuccess: (response, variables) => {
        const data = extractDataFromResponse(response);
        const convoId = variables?.conversationId;
        if (!data || !convoId) return;

        clearFreshConversation?.(convoId);

        // Update selected conversation
        setSelectedConversation?.((prev) => {
          if (!prev || prev.id !== convoId) return prev;
          const nextMeta = { ...(prev.meta || {}) };
          if (data.last_message_id !== undefined) {
            nextMeta.summary_last_message_id = data.last_message_id;
          }
          return {
            ...prev,
            summary_text: data.summary ?? prev.summary_text,
            meta: nextMeta,
          };
        });

        // Update conversations cache list
        if (conversationQueryKey) {
          queryClient.setQueryData(conversationQueryKey, (oldData) => {
            const arr = extractDataFromResponse(oldData);
            if (!Array.isArray(arr)) return oldData;
            const updated = arr.map((c) => {
              if (c.id !== convoId) return c;
              const nextMeta = { ...(c.meta || {}) };
              if (data.last_message_id !== undefined) {
                nextMeta.summary_last_message_id = data.last_message_id;
              }
              return {
                ...c,
                summary_text: data.summary ?? c.summary_text,
                meta: nextMeta,
              };
            });
            return { ...oldData, data: { data: updated } };
          });
        }
      },
    }
  );

  const autoRenameMutation = useMutation(
    ({ conversationId, payload = {} }) =>
      sideCallsAPI.autoRename(conversationId, payload),
    {
      onSuccess: (response, variables) => {
        const renameResult = extractDataFromResponse(response);
        const convoId = variables?.conversationId;
        if (!renameResult || !convoId) return;

        clearFreshConversation?.(convoId);

        // Update selected conversation title/meta
        setSelectedConversation?.((prev) => {
          if (!prev || prev.id !== convoId) return prev;
          const nextMeta = { ...(prev.meta || {}) };
          if (renameResult.last_message_id !== undefined) {
            nextMeta.last_auto_title_message_id = renameResult.last_message_id;
          }
          if (renameResult.title_locked !== undefined) {
            nextMeta.title_locked = renameResult.title_locked;
          }
          return {
            ...prev,
            title: renameResult.title ?? prev.title,
            meta: nextMeta,
          };
        });

        // Update conversations cache list
        if (conversationQueryKey) {
          queryClient.setQueryData(conversationQueryKey, (oldData) => {
            const arr = extractDataFromResponse(oldData);
            if (!Array.isArray(arr)) return oldData;
            const updated = arr.map((c) => {
              if (c.id !== convoId) return c;
              const nextMeta = { ...(c.meta || {}) };
              if (renameResult.last_message_id !== undefined) {
                nextMeta.last_auto_title_message_id = renameResult.last_message_id;
              }
              if (renameResult.title_locked !== undefined) {
                nextMeta.title_locked = renameResult.title_locked;
              }
              return {
                ...c,
                title: renameResult.title ?? c.title,
                meta: nextMeta,
              };
            });
            return { ...oldData, data: { data: updated } };
          });
        }
      },
      onError: (err) => {
        // Don't block sending on rename errors; just surface a notice
        handleError(err, 'Auto-rename failed');
      },
    }
  );

  const unlockRenameMutation = useMutation(
    (conversationId) => sideCallsAPI.unlockAutoRename(conversationId),
    {
      onError: (err) => {
        handleError(err, 'Failed to unlock auto-rename');
      },
      onSuccess: (response, conversationId) => {
        const payload = extractDataFromResponse(response);
        setSelectedConversation?.((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const nextMeta = {
            ...(prev.meta || {}),
            title_locked: payload?.title_locked ?? false,
          };
          return { ...prev, meta: nextMeta };
        });
      },
    }
  );

  const summaryIsLoading = generateSummaryMutation.isLoading;
  const autoRenameIsLoading = autoRenameMutation.isLoading;
  const unlockRenameIsLoading = unlockRenameMutation.isLoading;

  const runSummaryAsync = generateSummaryMutation.mutateAsync;
  const runAutoRename = autoRenameMutation.mutate;
  const runAutoRenameAsync = autoRenameMutation.mutateAsync;
  const unlockRenameAsync = unlockRenameMutation.mutateAsync;

  return {
    summaryIsLoading,
    autoRenameIsLoading,
    unlockRenameIsLoading,
    runSummaryAsync,
    runAutoRename,
    runAutoRenameAsync,
    unlockRenameAsync,
  };
};

export default useConversationLifecycle;

