import { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import log from '../utils/log';
import { useTheme as useMuiTheme } from '@mui/material/styles';

import {
  chatAPI,
  userPreferencesAPI,
  extractDataFromResponse,
  formatError,
  llmAPI,
  sideCallsAPI,
} from '../services/api';
import { buildUserPreferencesPayload } from '../utils/userPreferences';

import { useAuth } from '../hooks/useAuth';
import { useMobileSidebar } from '../contexts/MobileSidebarContext';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import useAutomationSettings from '../hooks/useAutomationSettings';
import { getBrandingAppName, getBrandingLogoUrl, RAG_REWRITE_OPTIONS } from '../utils/constants';
import { createChatStyles, attachmentChipStyles } from './chat/ModernChat/styles';
import { buildRenamePayloadBase, getLatestUserMessageContent } from './chat/ModernChat/utils/renamePayload';
import useChatComposer from './chat/ModernChat/hooks/useChatComposer';
import useChatStreaming from './chat/ModernChat/hooks/useChatStreaming';
import useSummarySearch from './chat/ModernChat/hooks/useSummarySearch';
import useMessageWindow from './chat/ModernChat/hooks/useMessageWindow';
import usePreferredModelConfig from './chat/ModernChat/hooks/usePreferredModelConfig';
import useFreshConversations from './chat/ModernChat/hooks/useFreshConversations';
import useConversationData from './chat/ModernChat/hooks/useConversationData';
import useConversationLifecycle from './chat/ModernChat/hooks/useConversationLifecycle';
import useConversationAutomation from './chat/ModernChat/hooks/useConversationAutomation';
import useMessageStream from './chat/ModernChat/hooks/useMessageStream';
import useVariantStreamingManager from './chat/ModernChat/hooks/useVariantStreamingManager';
import useSideBySideManager from './chat/ModernChat/hooks/useSideBySideManager';
import useChatUiState from './chat/ModernChat/hooks/useChatUiState';
import usePluginFlow from './chat/ModernChat/hooks/usePluginFlow';
import ModernChatView from './chat/ModernChat/ModernChatView';
import useEnsembleMode from './chat/ModernChat/hooks/useEnsembleMode';

import { CHAT_WINDOW_SIZE, CHAT_OVERSCAN, SUMMARY_SEARCH_DEBOUNCE_MS, DEFAULT_SUMMARY_SEARCH_MIN_TERM_LENGTH, DEFAULT_SUMMARY_SEARCH_MAX_TOKENS, STORAGE_KEY_RAG_REWRITE_MODE, DEFAULT_NEW_CHAT_TITLE, CHAT_PLUGINS_ENABLED } from './chat/ModernChat/utils/chatConfig';

const SIDE_CALL_NOT_CONFIGURED_TOOLTIP =
  'Side-caller agent not configured. Configure a model configuration and set it as the side-caller to restore conversation naming and summary generation functionality.';

const ModernChat = () => {
  const theme = useMuiTheme();
  const chatStyles = useMemo(() => createChatStyles(theme), [theme]);
  const attachmentChipSx = attachmentChipStyles;
  const { branding, theme: appTheme } = useAppTheme();
  const appDisplayName = getBrandingAppName(branding);
  const logoUrl = getBrandingLogoUrl(branding);
  const primaryMain = appTheme.palette.primary.main;

  const pluginsEnabled = CHAT_PLUGINS_ENABLED;

  const queryClient = useQueryClient();
  const { user, canManageUsers } = useAuth();
  const messageListRef = useRef(null);
  const fileInputRef = useRef(null);
  const inputRef = useRef(null);
  const isPinnedToBottomRef = useRef(true);
  const clearEnsembleModeRef = useRef(() => { });
  const {
    markFreshConversation,
    clearFreshConversation,
    isConversationFresh,
  } = useFreshConversations();
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true);

  // State management
  const [selectedConversation, setSelectedConversation] = useState(null);
  const selectedConversationRef = useRef(null);
  const [error, setError] = useState(null);
  const [streamingConversationId, setStreamingConversationId] = useState(null);
  const [, setStreamingStarted] = useState(false);
  const {
    summaryAnchorEl,
    openSummaryMenu,
    closeSummaryMenu,
    automationAnchorEl,
    openAutomationMenu,
    closeAutomationMenu,
    deleteDialogOpen,
    openDeleteDialog,
    closeDeleteDialog,
    renameDialog,
    renameError,
    openRenameDialog,
    closeRenameDialog,
    updateRenameValue,
    setRenameError: setRenameErrorState,
    settingsDialogOpen,
    openSettingsDialog,
    closeSettingsDialog,
    documentPreview,
    openDocumentPreview,
    closeDocumentPreview,
  } = useChatUiState();

  // Mobile sidebar state from context (shared with TopBar in UserLayout)
  const { isOpen: mobileSidebarOpen, close: closeMobileSidebar, toggle: toggleMobileSidebar } = useMobileSidebar();

  useEffect(() => {
    selectedConversationRef.current = selectedConversation;
  }, [selectedConversation]);
  // User preferences state (only legitimate user preferences)
  // RAG and LLM settings are now admin-only configuration
  const [userPreferences, setUserPreferences] = useState({
    memory_depth: 5,
    memory_similarity_threshold: 0.6,
    theme: 'light',
    language: 'en',
    timezone: 'UTC',
    advanced_settings: {},
    summary_search_min_token_length: DEFAULT_SUMMARY_SEARCH_MIN_TERM_LENGTH,
    summary_search_max_tokens: DEFAULT_SUMMARY_SEARCH_MAX_TOKENS,
  });

  // Side-caller state used to gate automation and show header warnings
  const [isSideCallConfigured, setIsSideCallConfigured] = useState(true);
  const [sideCallWarning, setSideCallWarning] = useState(null);
  const {
    searchInput: summarySearchInput,
    setSearchInput: setSummarySearchInput,
    summaryFeedback: summarySearchFeedback,
    summaryQuery,
  } = useSummarySearch({
    minTokenLength: userPreferences.summary_search_min_token_length ?? DEFAULT_SUMMARY_SEARCH_MIN_TERM_LENGTH,
    maxTokens: userPreferences.summary_search_max_tokens ?? DEFAULT_SUMMARY_SEARCH_MAX_TOKENS,
    debounceMs: SUMMARY_SEARCH_DEBOUNCE_MS,
  });
  const {
    conversations,
    conversationQueryKey,
    loadingConversations,
    modelConfigs,
    availableModelConfigs,
    loadingConfigs,
    createConversationMutation,
    lastCreatedConversation,
    resetLastCreatedConversation,
  } = useConversationData({
    summaryQuery,
    onQueryError: (message) => setError(message),
    onMutationError: (message) => setError(message),
    markFreshConversation,
  });

  const {
    chatPluginsSummaryText,
    showPluginInfoBanner,
    pluginPickerOpen,
    openPluginPicker,
    closePluginPicker,
    pluginModalOpen,
    closePluginModal,
    selectedPlugin,
    selectPlugin,
    pluginRun,
    setPluginRun,
  } = usePluginFlow({ pluginsEnabled });
  const {
    ensembleModeConfigIds,
    isEnsembleModeActive,
    ensembleModeLabel,
    ensembleDialogOpen,
    openEnsembleDialog,
    closeEnsembleDialog,
    applyEnsembleSelection,
    clearEnsembleSelection,
    canConfigureEnsemble,
  } = useEnsembleMode(availableModelConfigs);
  useEffect(() => {
    clearEnsembleModeRef.current = clearEnsembleSelection;
  }, [clearEnsembleSelection]);
  // Local-only automation cadence settings
  const [automationSettings, updateAutomationSettings] = useAutomationSettings();
  const [searchParams, setSearchParams] = useSearchParams();
  const CLEAR_PARAM_SENTINEL = '__cleared__';
  const pendingConversationParamRef = useRef(null);
  const syncConversationParam = useCallback(
    (conversationId) => {
      pendingConversationParamRef.current = conversationId ?? CLEAR_PARAM_SENTINEL;
      const next = new URLSearchParams(searchParams);
      if (conversationId) {
        next.set('conversationId', conversationId);
      } else {
        next.delete('conversationId');
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams]
  );

  const fallbackModelConfig = useMemo(() => {
    const mc = selectedConversation?.model_configuration;
    if (!mc) {
      return null;
    }
    return {
      id: mc.id,
      name: mc.name,
      model_name: mc.model_name,
      provider: mc.llm_provider
        ? {
          id: mc.llm_provider.id,
          name: mc.llm_provider.name,
          provider_type: mc.llm_provider.provider_type,
        }
        : undefined,
    };
  }, [selectedConversation]);

  // Track initial auto-rename trigger to avoid duplicate runs
  const initialRenameTriggeredRef = useRef(new Set());

  // Fetch model configurations
  const parseDocumentHref = useCallback((href) => {
    if (!href) {
      return null;
    }
    try {
      const url = new URL(href, window.location.origin);
      if (url.origin !== window.location.origin) {
        return null;
      }
      const segments = url.pathname.split('/').filter(Boolean);
      if (segments.length >= 3 && segments[0] === 'documents') {
        return {
          kbId: segments[1],
          documentId: segments[2],
        };
      }
      return null;
    } catch (err) {
      return null;
    }
  }, []);

  const [ragRewriteMode, setRagRewriteMode] = useState(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY_RAG_REWRITE_MODE);
      if (stored && stored === 'no_rag') {
        return 'raw_query';
      }
      if (stored && RAG_REWRITE_OPTIONS.some(opt => opt.value === stored)) {
        return stored;
      }
    } catch (_) { }
    return 'raw_query';
  });

  useEffect(() => {
    try {
      if (ragRewriteMode) {
        localStorage.setItem(STORAGE_KEY_RAG_REWRITE_MODE, ragRewriteMode);
      }
    } catch (err) {
      log.warn('Failed to persist RAG mode to storage:', err);
    }
  }, [ragRewriteMode]);

  const { isLoading: loadingModels } = useQuery(
    'llm-models',
    () => llmAPI.getModels(),
    {
      onError: (err) => {
        setError(formatError(err).message);
      }
    }
  );

  useQuery(
    'side-call-config',
    () => sideCallsAPI.getConfig(),
    {
      enabled: canManageUsers(),
      select: (data) => data ?? null,
      onSuccess: (config) => {
        const configured = Boolean(config?.side_call_model_config);
        setIsSideCallConfigured(configured);
        setSideCallWarning(
          configured ? null : SIDE_CALL_NOT_CONFIGURED_TOOLTIP
        );
      },
      onError: (err) => {
        // For non-admin users this will typically be 403; log but do not
        // treat it as "not configured" so we don't block automation.
        log.warn(
          'Failed to fetch side-call config:',
          formatError(err).message
        );
      },
    }
  );


  const {
    summaryIsLoading,
    autoRenameIsLoading,
    unlockRenameIsLoading,
    runSummaryAsync,
    runAutoRename,
    runAutoRenameAsync,
    unlockRenameAsync,
  } = useConversationLifecycle({
    conversationQueryKey,
    clearFreshConversation,
    setSelectedConversation,
    onError: (message) => setError(message),
    onSideCallNotConfigured: () => {
      setIsSideCallConfigured(false);
      setSideCallWarning(SIDE_CALL_NOT_CONFIGURED_TOOLTIP);
    },
  });

  // Fetch user preferences
  useQuery(
    'user-preferences',
    userPreferencesAPI.getPreferences,
    {
      onSuccess: (response) => {
        const preferences = extractDataFromResponse(response);
        if (preferences && typeof preferences === 'object') {
          setUserPreferences((prev) => ({
            ...prev,
            ...preferences,
            advanced_settings: preferences.advanced_settings ?? prev.advanced_settings ?? {},
            summary_search_min_token_length: preferences.summary_search_min_token_length ?? prev.summary_search_min_token_length,
            summary_search_max_tokens: preferences.summary_search_max_tokens ?? prev.summary_search_max_tokens,
          }));
        }
      },
      onError: (err) => {
        log.warn('Failed to load user preferences:', formatError(err).message);
        // Don't show error to user for preferences - use defaults
      }
    }
  );

  const switchModelMutation = useMutation(
    ({ conversationId, payload }) => chatAPI.switchConversationModel(conversationId, payload),
    {
      onSuccess: (response, variables) => {
        const apiConversation = extractDataFromResponse(response);
        const { config, conversationId, payload } = variables;
        const resolvedConfig = apiConversation?.model_configuration || config || null;
        const resolvedConfigId = apiConversation?.model_configuration_id || resolvedConfig?.id || config?.id || payload?.model_configuration_id || null;
        let mergedConversation = null;

        if (apiConversation) {
          mergedConversation = {
            ...apiConversation,
            model_configuration: resolvedConfig,
            model_configuration_id: resolvedConfigId,
          };
        } else if (selectedConversation?.id === conversationId) {
          mergedConversation = {
            ...selectedConversation,
            model_configuration: resolvedConfig || selectedConversation.model_configuration,
            model_configuration_id: resolvedConfigId || selectedConversation.model_configuration_id,
          };
        }

        if (mergedConversation) {
          setSelectedConversation(mergedConversation);
          if (resolvedConfigId) {
            selectPreferredModelConfig(resolvedConfigId);
          }
        }

        queryClient.setQueryData(conversationQueryKey, (oldData) => {
          const existing = extractDataFromResponse(oldData);
          if (!Array.isArray(existing)) return oldData;
          const updated = existing.map((conv) => {
            if (conv.id !== conversationId) {
              return conv;
            }
            return {
              ...conv,
              model_configuration: resolvedConfig || conv.model_configuration,
              model_configuration_id: resolvedConfigId || conv.model_configuration_id,
            };
          });
          return { ...oldData, data: { data: updated } };
        });

        setError(null);
        // Refresh conversations so sidebar metadata like descriptions reflect the new model
        queryClient.invalidateQueries('conversations');
      },
      onError: (err, variables) => {
        const formatted = formatError(err);
        setError(formatted?.message || 'Failed to switch model');
        if (variables?.previousConfigId) {
          selectPreferredModelConfig(variables.previousConfigId);
        } else if (selectedConversation?.model_configuration_id) {
          selectPreferredModelConfig(selectedConversation.model_configuration_id);
        }
      }
    }
  );

  const handleUnlockAutoRename = useCallback(async () => {
    if (!selectedConversation?.id) {
      closeAutomationMenu();
      return;
    }
    try {
      await unlockRenameAsync(selectedConversation.id);
    } catch (_) {
      // errors handled in mutation
    } finally {
      closeAutomationMenu();
    }
  }, [selectedConversation?.id, unlockRenameAsync, closeAutomationMenu]);

  // moved below after dependencies are defined


  const handleUserPreferencesChange = useCallback((updates) => {
    setUserPreferences((prev) => ({ ...prev, ...updates }));
  }, []);

  const handleAutomationSettingsChange = useCallback((updates) => {
    updateAutomationSettings(updates);
  }, [updateAutomationSettings]);

  const handleCopyMessage = useCallback((content) => {
    if (!content) return;
    if (navigator?.clipboard?.writeText) {
      navigator.clipboard.writeText(content).catch(() => { });
    }
  }, []);

  // Delete conversation mutation
  const deleteConversationMutation = useMutation(
    (conversationId) => chatAPI.deleteConversation(conversationId),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('conversations');
        setSelectedConversation(null);
        syncConversationParam(null);
        closeDeleteDialog();
        setError(null);
      },
      onError: (err) => {
        setError(formatError(err).message);
        closeDeleteDialog();
      }
    }
  );

  const renameConversationMutation = useMutation(
    ({ conversationId, title }) => chatAPI.updateConversation(conversationId, { title }),
    {
      onSuccess: (response, variables) => {
        const updatedConversation = extractDataFromResponse(response);
        const nextTitle = typeof updatedConversation?.title === 'string' ? updatedConversation.title : variables.title;

        if (variables?.conversationId) {
          clearFreshConversation(variables.conversationId);
        }
        queryClient.invalidateQueries('conversations');
        setSelectedConversation((prev) => {
          if (!prev || prev.id !== variables.conversationId) {
            return prev;
          }
          const merged = (updatedConversation && typeof updatedConversation === 'object') ? updatedConversation : {};
          return { ...prev, ...merged, title: nextTitle };
        });
        closeRenameDialog();
        setRenameErrorState('');
        setError(null);
      },
      onError: (err) => {
        const renameMessage = formatError(err);
        setRenameErrorState(
          typeof renameMessage === 'string' ? renameMessage : renameMessage?.message || 'Failed to rename conversation'
        );
      }
    }
  );

  // Update user preferences mutation
  const updatePreferencesMutation = useMutation(
    (preferences) => userPreferencesAPI.updatePreferences(buildUserPreferencesPayload(preferences)),
    {
      onSuccess: (response) => {
        const updatedPreferences = extractDataFromResponse(response);
        if (updatedPreferences && typeof updatedPreferences === 'object') {
          setUserPreferences((prev) => ({
            ...prev,
            ...updatedPreferences,
            advanced_settings: updatedPreferences.advanced_settings ?? prev.advanced_settings ?? {},
            summary_search_min_token_length: updatedPreferences.summary_search_min_token_length ?? prev.summary_search_min_token_length,
            summary_search_max_tokens: updatedPreferences.summary_search_max_tokens ?? prev.summary_search_max_tokens,
          }));
        }
        queryClient.invalidateQueries('user-preferences');
        closeSettingsDialog();
        setError(null);
      },
      onError: (err) => {
        setError(formatError(err).message);
      }
    }
  );

  const {
    preferredModelConfig,
    selectPreferredModelConfig,
    resolveInitialModelConfig,
  } = usePreferredModelConfig(availableModelConfigs, selectedConversation);

  useEffect(() => {
    if (!lastCreatedConversation) {
      return;
    }
    const config = modelConfigs.find((cfg) => cfg.id === lastCreatedConversation.model_configuration_id);
    if (config) {
      selectPreferredModelConfig(config.id);
    }
    setSelectedConversation(lastCreatedConversation);
    if (lastCreatedConversation?.id) {
      syncConversationParam(lastCreatedConversation.id);
    }
    setError(null);
    resetLastCreatedConversation();
  }, [
    lastCreatedConversation,
    modelConfigs,
    selectPreferredModelConfig,
    resetLastCreatedConversation,
    syncConversationParam,
  ]);

  const selectedModelConfig = preferredModelConfig;

  const scrollToBottom = useCallback((behavior = 'auto') => {
    messageListRef.current?.scrollToBottom(behavior);
  }, []);

  const captureScrollSnapshot = useCallback(() => messageListRef.current?.captureScrollSnapshot(), []);

  const restoreScrollSnapshot = useCallback((snapshot) => {
    if (snapshot) {
      messageListRef.current?.restoreScrollSnapshot(snapshot);
    }
  }, []);

  const scheduleScrollToBottom = useCallback(
    (behavior = 'auto') => {
      requestAnimationFrame(() => scrollToBottom(behavior));
    },
    [scrollToBottom]
  );

  const {
    messages,
    loadingMessages,
    hasMoreMessages,
    loadingOlderMessages,
    loadOlderMessages,
  } = useMessageStream({
    selectedConversation,
    queryClient,
    setError,
    scheduleScrollToBottom,
    setIsPinnedToBottom,
    clearFreshConversation,
    markFreshConversation,
  });

  const {
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
  } = useVariantStreamingManager({
    messages,
    selectedConversationId: selectedConversation?.id,
  });

  const {
    sideBySideParents,
    toggleSideBySide,
    replaceSideBySideParent,
    registerRegenerationStart,
    registerRegenerationComplete,
    enqueueAutoEnable,
    handleToggleReasoning,
  } = useSideBySideManager({
    variantGroups,
    streamingVariantParentIds,
    regenerationRequests,
    queryClient,
    selectedConversationId: selectedConversation?.id,
  });

  const startRegenerationWithSideBySide = useCallback(
    (messageId, parentId, tempId) => {
      registerRegenerationStart(messageId, parentId);
      startRegeneration(messageId, parentId, tempId);
    },
    [registerRegenerationStart, startRegeneration]
  );

  const completeRegenerationWithSideBySide = useCallback(
    (messageId) => {
      registerRegenerationComplete(messageId);
      completeRegeneration(messageId);
    },
    [registerRegenerationComplete, completeRegeneration]
  );

  const focusMessageById = useCallback((messageId, options = {}) => {
    if (!messageId) return;
    messageListRef.current?.scrollToMessage(messageId, {
      align: options.align || 'start',
      behavior: options.behavior || 'auto',
    });
  }, []);

  const {
    handleStreamingResponse,
    handleRegenerate,
  } = useChatStreaming({
    queryClient,
    setError,
    setStreamingConversationId,
    setStreamingStarted,
    inputRef,
    selectedConversation,
    userPreferences,
    setVariantSelection,
    startRegeneration: startRegenerationWithSideBySide,
    completeRegeneration: completeRegenerationWithSideBySide,
    ragRewriteMode,
    scheduleScrollToBottom,
    shouldAutoFollowRef: isPinnedToBottomRef,
    focusMessageById,
    replaceSideBySideParent,
  });

  const {
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
  } = useChatComposer({
    selectedConversation,
    messages,
    userPreferences,
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
    onSlashCommand: openPluginPicker,
    inputRef,
    fileInputRef,
    scheduleScrollToBottom,
    ragRewriteMode,
    ensembleModelConfigIds: ensembleModeConfigIds,
    onEnsembleRunComplete: clearEnsembleSelection,
  });

  const isStreamingForSelectedConversation =
    streamingConversationId === selectedConversation?.id;

  const isSendDisabled =
    isStreamingForSelectedConversation;

  const { visibleMessages: windowMessages, expandWindow, advanceWindow, visibleOffset, windowStart } = useMessageWindow(
    flattenedMessages,
    { windowSize: CHAT_WINDOW_SIZE, overscan: CHAT_OVERSCAN, pinned: isPinnedToBottom }
  );
  const windowMessageCount = windowMessages.length;

  useEffect(() => {
    if (isPinnedToBottom) {
      scheduleScrollToBottom('auto');
    }
  }, [isPinnedToBottom, scheduleScrollToBottom, windowMessageCount]);

  const handleBottomStateChange = useCallback(
    (atBottom) => {
      setIsPinnedToBottom(atBottom);
      if (atBottom) {
        scheduleScrollToBottom('auto');
      }
    },
    [scheduleScrollToBottom]
  );

  const handleUserInteract = useCallback(() => {
    setIsPinnedToBottom(false);
  }, []);

  const handleLoadOlderMessages = useCallback(() => {
    loadOlderMessages({
      captureScrollSnapshot,
      restoreScrollSnapshot,
      expandWindow,
    });
  }, [loadOlderMessages, captureScrollSnapshot, restoreScrollSnapshot, expandWindow]);

  const handleRevealOlderInMemory = useCallback(() => {
    // Reveal older messages that are already in memory (no server fetch required)
    if (visibleOffset <= 0) {
      return;
    }
    const snapshot = captureScrollSnapshot();
    setIsPinnedToBottom(false);
    const step = Math.min(visibleOffset, CHAT_WINDOW_SIZE);
    expandWindow(step);
    requestAnimationFrame(() => {
      restoreScrollSnapshot(snapshot);
    });
  }, [visibleOffset, expandWindow, captureScrollSnapshot, restoreScrollSnapshot]);

  const handleRevealNewerInMemory = useCallback(() => {
    // Reveal newer messages that are already in memory (no server fetch required)
    const total = Array.isArray(flattenedMessages) ? flattenedMessages.length : 0;
    const currentStart = windowStart;
    const currentCount = CHAT_WINDOW_SIZE;
    const remainingBelow = Math.max(total - (currentStart + currentCount), 0);
    if (remainingBelow <= 0) {
      return;
    }
    const snapshot = captureScrollSnapshot();
    setIsPinnedToBottom(false);
    const step = Math.min(remainingBelow, CHAT_WINDOW_SIZE);
    advanceWindow(step);
    requestAnimationFrame(() => {
      restoreScrollSnapshot(snapshot);
    });
  }, [flattenedMessages, windowStart, advanceWindow, captureScrollSnapshot, restoreScrollSnapshot]);


  const latestUserMessageContent = useMemo(
    () => getLatestUserMessageContent(flattenedMessages),
    [flattenedMessages]
  );

  const buildRenamePayload = useCallback(
    (explicitFallback) => buildRenamePayloadBase(latestUserMessageContent, explicitFallback),
    [latestUserMessageContent]
  );

  const handleRunSummaryAndRename = useCallback(async () => {
    try {
      const convoId = selectedConversation?.id;
      if (convoId) {
        await runSummaryAsync({ conversationId: convoId, payload: {} });
        if (!selectedConversation?.meta?.title_locked) {
          await runAutoRenameAsync({
            conversationId: selectedConversation.id,
            payload: buildRenamePayload(),
          });
        }
      }
    } catch (_) {
      // handled in mutations
    } finally {
      closeAutomationMenu();
    }
  }, [selectedConversation?.id, selectedConversation?.meta?.title_locked, runSummaryAsync, runAutoRenameAsync, buildRenamePayload, closeAutomationMenu]);

  // Assistant reply count and latest assistant message id (collapsed variants, ignore placeholders)
  const { assistantReplyCount, lastAssistantMessageId, messagesMatchSelectedConversation } = useMemo(() => {
    const all = Array.isArray(flattenedMessages) ? flattenedMessages : [];
    const matches = all.every((m) => !m?.conversation_id || m.conversation_id === selectedConversation?.id);
    if (!matches) {
      return { assistantReplyCount: 0, lastAssistantMessageId: null, messagesMatchSelectedConversation: false };
    }
    const assistants = all.filter((m) => m.role === 'assistant' && !m.isPlaceholder);
    const count = assistants.length;
    const lastId = count > 0 ? assistants[assistants.length - 1].id : null;
    return { assistantReplyCount: count, lastAssistantMessageId: lastId, messagesMatchSelectedConversation: true };
  }, [flattenedMessages, selectedConversation?.id]);

  // Automation cadence: summary and auto-rename based on assistant reply count
  useConversationAutomation({
    selectedConversation,
    assistantReplyCount,
    lastAssistantMessageId,
    automationSettings,
    messagesMatchSelectedConversation,
    summaryIsLoading,
    autoRenameIsLoading,
    runSummaryAsync,
    runAutoRenameAsync,
    buildRenamePayload,
    isConversationFresh,
  });

  useEffect(() => {
    isPinnedToBottomRef.current = isPinnedToBottom;
  }, [isPinnedToBottom]);

  useEffect(() => {
    resolveInitialModelConfig();
  }, [resolveInitialModelConfig]);

  useEffect(() => {
    if (selectedConversation && inputRef.current) {
      setTimeout(() => {
        inputRef.current?.focus();
      }, 100);
    }
  }, [selectedConversation]);

  useEffect(() => {
    const cid = searchParams.get('conversationId');
    const pendingTarget = pendingConversationParamRef.current;
    if (pendingTarget) {
      const matches =
        pendingTarget === CLEAR_PARAM_SENTINEL ? !cid : cid === pendingTarget;
      if (matches) {
        pendingConversationParamRef.current = null;
        if (pendingTarget === CLEAR_PARAM_SENTINEL) {
          if (selectedConversationRef.current) {
            setSelectedConversation(null);
          }
          return;
        }
        if (selectedConversationRef.current?.id === pendingTarget) {
          return;
        }
      }
    }

    if (!cid) {
      return;
    }

    const currentSelected = selectedConversationRef.current;
    if (currentSelected?.id === cid) {
      return;
    }

    const found = Array.isArray(conversations) ? conversations.find(c => c.id === cid) : null;
    if (found) {
      setSelectedConversation(found);
      return;
    }

    (async () => {
      try {
        const resp = await chatAPI.getConversation(cid);
        const conv = extractDataFromResponse(resp);
        if (conv) setSelectedConversation(conv);
      } catch (_) {
        // ignore if not found or unauthorized
      }
    })();
  }, [searchParams, conversations]);

  const handleCreateConversation = () => {
    if (!Array.isArray(availableModelConfigs) || availableModelConfigs.length === 0) {
      setError('No model configurations available');
      return;
    }

    const persistedPreference = preferredModelConfig || null;

    const preferredConfigId = selectedConversation?.model_configuration_id || selectedModelConfig || persistedPreference || availableModelConfigs[0]?.id;
    let selectedConfig = null;
    if (preferredConfigId) {
      selectedConfig = availableModelConfigs.find(cfg => cfg.id === preferredConfigId) || null;
    }
    if (!selectedConfig) {
      selectedConfig = availableModelConfigs[0];
    }

    if (!selectedConfig?.id) {
      setError('Unable to choose a model configuration. Please try again.');
      return;
    }

    selectPreferredModelConfig(selectedConfig.id);
    setError(null);
    createConversationMutation.mutate({
      model_configuration_id: selectedConfig.id,
      title: DEFAULT_NEW_CHAT_TITLE
    });
  };

  const handleModelConfigChange = (event) => {
    const newConfigId = event.target.value;
    selectPreferredModelConfig(newConfigId);

    if (!selectedConversation) {
      return;
    }

    if (newConfigId === selectedConversation.model_configuration_id) {
      return;
    }

    const config = availableModelConfigs.find(cfg => cfg.id === newConfigId);
    if (!config) {
      return;
    }

    setError(null);
    switchModelMutation.mutate({
      conversationId: selectedConversation.id,
      payload: {
        model_configuration_id: config.id
      },
      config,
      previousConfigId: selectedConversation.model_configuration_id
    });
  };

  const handleOpenRenameDialog = (conversation) => {
    renameConversationMutation.reset();
    openRenameDialog(conversation);
  };

  const handleCloseRenameDialog = () => {
    renameConversationMutation.reset();
    closeRenameDialog();
  };

  const handleRenameInputChange = (event) => {
    const { value } = event.target;
    updateRenameValue(value);
  };

  const handleConfirmRename = () => {
    const activeConversation = renameDialog.conversation;
    if (!activeConversation) {
      return;
    }

    const trimmedTitle = renameDialog.value.trim();
    if (!trimmedTitle) {
      setRenameErrorState('Title is required');
      return;
    }

    if (trimmedTitle === activeConversation.title) {
      handleCloseRenameDialog();
      return;
    }

    renameConversationMutation.mutate({
      conversationId: activeConversation.id,
      title: trimmedTitle,
    });
  };

  const handleConfirmDelete = () => {
    if (selectedConversation) {
      deleteConversationMutation.mutate(selectedConversation.id);
    }
  };

  const getSelectedConfig = () => {
    return availableModelConfigs.find(config => config.id === selectedModelConfig);
  };

  const handleSelectConversation = useCallback((conversation) => {
    setSelectedConversation(conversation);
    setStreamingConversationId(null);
    setStreamingStarted(false);
    syncConversationParam(conversation?.id || null);
    closeMobileSidebar();
  }, [syncConversationParam, closeMobileSidebar]);

  const handleSummarySearchChange = useCallback((value) => {
    setSummarySearchInput(value);
  }, [setSummarySearchInput]);

  const handleOpenDeleteDialog = useCallback((conversation) => {
    setSelectedConversation(conversation);
    openDeleteDialog();
  }, [openDeleteDialog]);

  const handleComposerSend = useCallback(() => {
    setIsPinnedToBottom(true);
    scheduleScrollToBottom('smooth');
    handleSendMessage();
  }, [scheduleScrollToBottom, handleSendMessage]);

  const createConversationDisabled = availableModelConfigs.length === 0 || createConversationMutation.isLoading;

  const conversationSidebarProps = {
    conversations,
    loadingConversations,
    selectedConversationId: selectedConversation?.id,
    onSelectConversation: handleSelectConversation,
    onCreateConversation: handleCreateConversation,
    createConversationDisabled,
    showNoModelsNote: availableModelConfigs.length === 0 && !loadingConfigs,
    onRenameConversation: handleOpenRenameDialog,
    onDeleteConversation: handleOpenDeleteDialog,
    branding: { appDisplayName, logoUrl, primaryMain },
    chatStyles,
    searchValue: summarySearchInput,
    onSearchChange: handleSummarySearchChange,
    searchFeedback: summarySearchFeedback,
  };

  const headerProps = {
    conversation: selectedConversation,
    isAutoRenaming: autoRenameIsLoading,
    onOpenSummary: (event) => openSummaryMenu(event.currentTarget),
    summaryAnchorEl,
    onCloseSummary: closeSummaryMenu,
    onOpenAutomationMenu: (event) => openAutomationMenu(event.currentTarget),
    availableModelConfigs,
    selectedModelConfig,
    onModelChange: handleModelConfigChange,
    disableModelSelect:
      !selectedConversation ||
      availableModelConfigs.length === 0 ||
      loadingConfigs ||
      loadingModels ||
      switchModelMutation.isLoading,
    onOpenSettings: openSettingsDialog,
    sideCallWarning,
  };

  const automationMenuProps = {
    anchorEl: automationAnchorEl,
    onClose: closeAutomationMenu,
    isTitleLocked: Boolean(selectedConversation?.meta?.title_locked),
    onUnlock: handleUnlockAutoRename,
    onRunSummaryAndRename: handleRunSummaryAndRename,
    disableUnlock: unlockRenameIsLoading,
    disableAutomation: summaryIsLoading || autoRenameIsLoading || !isSideCallConfigured,
  };

  const messageListProps = {
    messages: windowMessages,
    loading: loadingMessages,
    user,
    theme,
    chatStyles,
    attachmentChipStyles: attachmentChipSx,
    variantGroups,
    variantSelection,
    onVariantChange: handleVariantChange,
    onRegenerate: handleRegenerate,
    onCopy: handleCopyMessage,
    isVariantGroupStreaming,
    parseDocumentHref,
    onOpenDocument: openDocumentPreview,
    fallbackModelConfig,
    regenerationRequests,
    sideBySideParents,
    onToggleSideBySide: toggleSideBySide,
    onBottomStateChange: handleBottomStateChange,
    onUserInteract: handleUserInteract,
    onLoadOlder: handleLoadOlderMessages,
    onRevealOlderInMemory: handleRevealOlderInMemory,
    onRevealNewerInMemory: handleRevealNewerInMemory,
    hasMore: hasMoreMessages,
    isLoadingOlder: loadingOlderMessages,
    baseIndex: visibleOffset,
    totalCount: flattenedMessages.length,
    onToggleReasoning: handleToggleReasoning,
  };

  const pluginRunPanelProps = {
    pluginRun,
    onClear: () => setPluginRun(null),
  };

  const documentPreviewProps = {
    open: documentPreview.open,
    onClose: closeDocumentPreview,
    kbId: documentPreview.kbId,
    documentId: documentPreview.documentId,
    maxChars: 1000,
    showExtractionDetails: false,
  };

  const inputBarProps = {
    pendingAttachments,
    onRemoveAttachment: removePendingAttachment,
    attachmentChipStyles: attachmentChipSx,
    inputMessage,
    onInputChange: handleInputChange,
    onKeyDown: handleKeyPress,
    onSend: handleComposerSend,
    sendDisabled: isSendDisabled,
    inputRef,
    fileInputRef,
    onFileSelected: handleFileSelected,
    plusAnchorEl,
    onPlusOpen: (target) => setPlusAnchorEl(target),
    onPlusClose: () => setPlusAnchorEl(null),
    isUploadingAttachment,
    onOpenPluginPicker: openPluginPicker,
    pluginsEnabled,
    onUploadClick: handleUploadClick,
    onSelectEnsembleMode: canConfigureEnsemble ? openEnsembleDialog : undefined,
    isEnsembleModeActive,
    ensembleModeLabel,
    onClearEnsembleMode: isEnsembleModeActive ? clearEnsembleSelection : undefined,
    ensembleMenuDisabled: !canConfigureEnsemble,
  };

  const pluginPickerDialogProps = {
    open: pluginPickerOpen,
    onClose: closePluginPicker,
    onSelect: selectPlugin,
  };

  const pluginExecutionModalProps = {
    open: pluginModalOpen,
    onClose: closePluginModal,
    plugin: selectedPlugin,
    onStart: ({ plugin }) => setPluginRun({ status: 'running', plugin }),
    onResult: (data, meta) => setPluginRun({ status: 'success', plugin: meta?.plugin || selectedPlugin, data }),
  };

  const ensembleDialogProps = {
    open: ensembleDialogOpen,
    onClose: closeEnsembleDialog,
    onSave: applyEnsembleSelection,
    availableModelConfigs,
    selectedIds: ensembleModeConfigIds,
  };

  const renameDialogProps = {
    open: renameDialog.open,
    value: renameDialog.value,
    error: renameError,
    onChange: handleRenameInputChange,
    onCancel: handleCloseRenameDialog,
    onConfirm: handleConfirmRename,
    isSaving: renameConversationMutation.isLoading,
  };

  const deleteDialogProps = {
    open: deleteDialogOpen,
    conversationTitle: selectedConversation?.title || '',
    onCancel: closeDeleteDialog,
    onConfirm: handleConfirmDelete,
    isDeleting: deleteConversationMutation.isLoading,
  };

  const settingsDialogProps = {
    open: settingsDialogOpen,
    onClose: closeSettingsDialog,
    userPreferences,
    onUserPreferencesChange: handleUserPreferencesChange,
    automationSettings,
    onAutomationSettingsChange: handleAutomationSettingsChange,
    onSave: () => updatePreferencesMutation.mutate(userPreferences),
    isSaving: updatePreferencesMutation.isLoading,
    ragRewriteMode,
    setRagRewriteMode,
    // Model configuration props for mobile users
    availableModelConfigs,
    selectedModelConfig,
    onModelChange: handleModelConfigChange,
    disableModelSelect: !selectedConversation || isStreamingForSelectedConversation,
  };

  return (
    <ModernChatView
      appDisplayName={appDisplayName}
      selectedConversation={selectedConversation}
      error={error}
      setError={setError}
      showPluginInfoBanner={showPluginInfoBanner}
      chatPluginsSummaryText={chatPluginsSummaryText}
      conversationSidebarProps={conversationSidebarProps}
      headerProps={headerProps}
      automationMenuProps={automationMenuProps}
      messageListProps={messageListProps}
      messageListRef={messageListRef}
      pluginRunPanelProps={pluginRunPanelProps}
      documentPreviewProps={documentPreviewProps}
      inputBarProps={inputBarProps}
      pluginPickerDialogProps={pluginPickerDialogProps}
      pluginExecutionModalProps={pluginExecutionModalProps}
      ensembleDialogProps={ensembleDialogProps}
      renameDialogProps={renameDialogProps}
      deleteDialogProps={deleteDialogProps}
      settingsDialogProps={settingsDialogProps}
      pluginsEnabled={pluginsEnabled}
      getSelectedConfig={getSelectedConfig}
      handleCreateConversation={handleCreateConversation}
      createConversationButtonDisabled={createConversationDisabled}
      mobileSidebarOpen={mobileSidebarOpen}
      onCloseMobileSidebar={closeMobileSidebar}
      onToggleMobileSidebar={toggleMobileSidebar}
    />
  );
};

export default ModernChat;
