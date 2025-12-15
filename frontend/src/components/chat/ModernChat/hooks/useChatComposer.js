import { useCallback, useMemo, useState } from 'react';
import { chatAPI, extractDataFromResponse, formatError } from '../../../../services/api';
import { buildRenamePayloadBase, getLatestUserMessageContent } from '../utils/renamePayload';
import { getMessagesFromCache, rebuildCache } from '../utils/chatCache';
import { PLACEHOLDER_THINKING } from '../utils/chatConfig';

const useChatComposer = ({
  selectedConversation,
  messages,
  automationSettings,
  runAutoRename,
  clearFreshConversation,
  handleStreamingResponse,
  queryClient,
  setError,
  setStreamingConversationId,
  setStreamingStarted,
  initialRenameTriggeredRef,
  pluginsEnabled,
  selectedPlugin,
  onSlashCommand,
  inputRef,
  fileInputRef,
  scheduleScrollToBottom,
  ragRewriteMode,
  ensembleModelConfigIds = [],
  onEnsembleRunComplete,
}) => {
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [inputMessage, setInputMessage] = useState('');
  const [isUploadingAttachment, setIsUploadingAttachment] = useState(false);
  const [plusAnchorEl, setPlusAnchorEl] = useState(null);

  const latestUserMessageContent = useMemo(
    () => getLatestUserMessageContent(messages),
    [messages]
  );

  const buildRenamePayload = useCallback(
    (explicitFallback) => buildRenamePayloadBase(latestUserMessageContent, explicitFallback),
    [latestUserMessageContent]
  );

  const handleUploadClick = useCallback(() => {
    fileInputRef.current?.click();
  }, [fileInputRef]);

  const handleFileSelected = useCallback(
    async (event) => {
      if (!selectedConversation?.id) {
        setError('Select or create a conversation before uploading');
        return;
      }

      const file = event.target.files?.[0];
      if (!file) {
        return;
      }

      setIsUploadingAttachment(true);
      try {
        const response = await chatAPI.uploadAttachment(selectedConversation.id, file);
        const data = extractDataFromResponse(response);

        if (data?.attachment_id) {
          setPendingAttachments((prev) => [...prev, { id: data.attachment_id, name: file.name }]);
        }
        event.target.value = '';
      } catch (error) {
        setError(`Attachment upload failed: ${formatError(error).message}`);
      } finally {
        setIsUploadingAttachment(false);
      }
    },
    [selectedConversation, setError]
  );

  const removePendingAttachment = useCallback((id) => {
    setPendingAttachments((prev) => prev.filter((attachment) => attachment.id !== id));
  }, []);

  const handleInputChange = useCallback(
    (event) => {
      const value = event.target.value;
      setInputMessage(value);
      if (pluginsEnabled && value.startsWith('/')) {
        onSlashCommand?.();
      }
    },
    [pluginsEnabled, onSlashCommand]
  );

  const handleSendMessage = useCallback(() => {
    if (!inputMessage.trim() || !selectedConversation?.id) {
      return;
    }

    const userMessage = inputMessage.trim();
    const conversationId = selectedConversation.id;
    if (conversationId) {
      clearFreshConversation?.(conversationId);
    }
    const existingUserMessages = Array.isArray(messages)
      ? messages.filter((message) => message.role === 'user').length
      : 0;
    const isTitleLocked = Boolean(selectedConversation?.meta?.title_locked);

    if (
      automationSettings.firstUserRename &&
      !isTitleLocked &&
      existingUserMessages === 0 &&
      conversationId &&
      !initialRenameTriggeredRef.current.has(conversationId)
    ) {
      initialRenameTriggeredRef.current.add(conversationId);
      runAutoRename({ conversationId, payload: buildRenamePayload(userMessage) });
    }

    const optimisticAttachments = pendingAttachments.map((attachment) => ({
      id: attachment.id,
      original_filename: attachment.name,
      expired: false,
    }));

    setPendingAttachments([]);
    setInputMessage('');
    setStreamingConversationId(conversationId);
    setStreamingStarted(false);
    setError(null);

    // Create a stable client-side temp id for the user message so the server can echo it
    // back in SSE as client_temp_id for deterministic replacement.
    const userTempId = `temp-${Date.now()}`;
    queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
      const existing = getMessagesFromCache(oldData);

      const newUserMessage = {
        id: userTempId,
        role: 'user',
        content: userMessage,
        created_at: new Date().toISOString(),
        conversation_id: conversationId,
        attachments: optimisticAttachments,
        // Mark as placeholder so it gets replaced by the persisted record on refresh/merge
        isPlaceholder: true,
      };

      return rebuildCache(oldData, [...existing, newUserMessage]);
    });

    const ensembleIds = Array.isArray(ensembleModelConfigIds)
      ? Array.from(new Set(ensembleModelConfigIds.filter(Boolean)))
      : [];

    const nowIso = new Date().toISOString();
    const placeholderRootId = `streaming-root-${Date.now()}`;
    const totalVariants = Math.max(1, 1 + ensembleIds.length);
    const placeholderIds = {};
    const placeholderMeta = {};

    queryClient.setQueryData(['conversation-messages', conversationId], (oldData) => {
      const existing = getMessagesFromCache(oldData);
      const placeholders = [];
      for (let idx = 0; idx < totalVariants; idx += 1) {
        const placeholderId = `${placeholderRootId}-variant-${idx}`;
        placeholderIds[idx] = placeholderId;
        placeholderMeta[idx] = { id: placeholderId, created_at: nowIso };
        placeholders.push({
          id: placeholderId,
          role: 'assistant',
          content: PLACEHOLDER_THINKING,
          created_at: nowIso,
          conversation_id: conversationId,
          isStreaming: true,
          isPlaceholder: true,
          parent_message_id: placeholderRootId,
          variant_index: idx,
        });
      }
      return rebuildCache(oldData, [...existing, ...placeholders]);
    });

    // Request scroll to bottom after placeholder is added
    scheduleScrollToBottom?.('smooth');

    const payload = {
      message: userMessage,
      rag_rewrite_mode: ragRewriteMode,
      client_temp_id: userTempId,
    };

    if (selectedPlugin) {
      payload.plugin_execution = {
        plugin: selectedPlugin,
      };
    }

    if (ensembleIds.length > 0) {
      payload.ensemble_model_configuration_ids = ensembleIds;
    }

    const defaultVariantIndex = totalVariants - 1; // backend appends conversation config last
    const primaryPlaceholderId = placeholderIds[defaultVariantIndex];
    handleStreamingResponse(conversationId, payload, {
      tempMessageId: primaryPlaceholderId,
      placeholderMap: placeholderIds,
      placeholderRootId,
      placeholderMeta,
      onComplete: onEnsembleRunComplete,
    });
  }, [
    inputMessage,
    selectedConversation,
    messages,
    automationSettings.firstUserRename,
    pendingAttachments,
    selectedPlugin,
    runAutoRename,
    clearFreshConversation,
    queryClient,
    setError,
    setStreamingConversationId,
    setStreamingStarted,
    handleStreamingResponse,
    initialRenameTriggeredRef,
    ragRewriteMode,
    buildRenamePayload,
    scheduleScrollToBottom,
    ensembleModelConfigIds,
    onEnsembleRunComplete,
  ]);

  const handleKeyPress = useCallback(
    (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        handleSendMessage();
      }
    },
    [handleSendMessage]
  );

  return {
    pendingAttachments,
    inputMessage,
    isUploadingAttachment,
    plusAnchorEl,
    setPlusAnchorEl,
    handleUploadClick,
    handleFileSelected,
    removePendingAttachment,
    handleInputChange,
    handleSendMessage,
    handleKeyPress,
  };
};

export default useChatComposer;
