import { useState, useCallback } from 'react';

const initialRenameState = {
  open: false,
  conversation: null,
  value: '',
};

const initialDocumentPreview = {
  open: false,
  kbId: null,
  documentId: null,
};

const useChatUiState = () => {
  const [summaryAnchorEl, setSummaryAnchorEl] = useState(null);
  const [automationAnchorEl, setAutomationAnchorEl] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [renameDialog, setRenameDialog] = useState(initialRenameState);
  const [renameError, setRenameError] = useState('');
  const [settingsDialogOpen, setSettingsDialogOpen] = useState(false);
  const [longConversationDialogOpen, setLongConversationDialogOpen] = useState(false);
  const [longConversationDismissedIds, setLongConversationDismissedIds] = useState(new Set());
  const [documentPreview, setDocumentPreview] = useState(initialDocumentPreview);

  const openSummaryMenu = useCallback((anchorEl) => {
    setSummaryAnchorEl(anchorEl || null);
  }, []);

  const closeSummaryMenu = useCallback(() => {
    setSummaryAnchorEl(null);
  }, []);

  const openAutomationMenu = useCallback((anchorEl) => {
    setAutomationAnchorEl(anchorEl || null);
  }, []);

  const closeAutomationMenu = useCallback(() => {
    setAutomationAnchorEl(null);
  }, []);

  const openDeleteDialog = useCallback(() => {
    setDeleteDialogOpen(true);
  }, []);

  const closeDeleteDialog = useCallback(() => {
    setDeleteDialogOpen(false);
  }, []);

  const openRenameDialog = useCallback((conversation) => {
    setRenameDialog({
      open: true,
      conversation,
      value: conversation?.title || '',
    });
    setRenameError('');
  }, []);

  const closeRenameDialog = useCallback(() => {
    setRenameDialog(initialRenameState);
    setRenameError('');
  }, []);

  const updateRenameValue = useCallback(
    (value) => {
      setRenameDialog((prev) => ({
        ...prev,
        value,
      }));
      if (renameError) {
        setRenameError('');
      }
    },
    [renameError]
  );

  const openSettingsDialog = useCallback(() => {
    setSettingsDialogOpen(true);
  }, []);

  const closeSettingsDialog = useCallback(() => {
    setSettingsDialogOpen(false);
  }, []);

  const openLongConversationDialog = useCallback(() => {
    setLongConversationDialogOpen(true);
  }, []);

  const closeLongConversationDialog = useCallback(() => {
    setLongConversationDialogOpen(false);
  }, []);

  const dismissLongConversationDialog = useCallback((conversationId) => {
    setLongConversationDialogOpen(false);
    if (conversationId) {
      setLongConversationDismissedIds((prev) => new Set(prev).add(conversationId));
    }
  }, []);

  const isLongConversationDismissed = useCallback(
    (conversationId) => longConversationDismissedIds.has(conversationId),
    [longConversationDismissedIds]
  );

  const openDocumentPreview = useCallback(({ kbId, documentId }) => {
    if (!kbId || !documentId) {
      return;
    }
    setDocumentPreview({
      open: true,
      kbId,
      documentId,
    });
  }, []);

  const closeDocumentPreview = useCallback(() => {
    setDocumentPreview(initialDocumentPreview);
  }, []);

  return {
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
    setRenameError,
    settingsDialogOpen,
    openSettingsDialog,
    closeSettingsDialog,
    documentPreview,
    openDocumentPreview,
    closeDocumentPreview,
    longConversationDialogOpen,
    openLongConversationDialog,
    closeLongConversationDialog,
    dismissLongConversationDialog,
    isLongConversationDismissed,
  };
};

export default useChatUiState;
