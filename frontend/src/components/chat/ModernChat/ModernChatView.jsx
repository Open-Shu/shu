import React from 'react';
import {
  Box,
  Paper,
  Typography,
  Button,
  Alert,
} from '@mui/material';
import {
  Add as AddIcon,
  SmartToy as BotIcon,
} from '@mui/icons-material';

import ConversationSidebar from './ConversationSidebar';
import ChatHeader from './ChatHeader';
import AutomationMenu from './AutomationMenu';
import MessageList from './MessageList';
import PluginRunPanel from './PluginRunPanel';
import DocumentPreview from '../../DocumentPreview';
import InputBar from './InputBar';
import PluginPickerDialog from '../../PluginPickerDialog';
import PluginExecutionModal from '../../PluginExecutionModal';
import EnsembleModeDialog from './EnsembleModeDialog';
import RenameConversationDialog from './RenameConversationDialog';
import DeleteConversationDialog from './DeleteConversationDialog';
import ChatSettingsDialog from './ChatSettingsDialog';

const ModernChatView = ({
  appDisplayName,
  selectedConversation,
  error,
  setError,
  showPluginInfoBanner,
  chatPluginsSummaryText,
  conversationSidebarProps,
  headerProps,
  automationMenuProps,
  messageListProps,
  messageListRef,
  pluginRunPanelProps,
  documentPreviewProps,
  inputBarProps,
  pluginPickerDialogProps,
  pluginExecutionModalProps,
  ensembleDialogProps,
  renameDialogProps,
  deleteDialogProps,
  settingsDialogProps,
  pluginsEnabled,
  getSelectedConfig,
  handleCreateConversation,
  createConversationButtonDisabled,
}) => (
  <>
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      <ConversationSidebar {...conversationSidebarProps} />

      <Box sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
        {selectedConversation ? (
          <>
            <ChatHeader {...headerProps} />

            <AutomationMenu {...automationMenuProps} />

            <MessageList
              ref={messageListRef}
              key={selectedConversation?.id || 'no-conversation'}
              {...messageListProps}
            />

            <PluginRunPanel {...pluginRunPanelProps} />

            <DocumentPreview {...documentPreviewProps} />

            <Paper sx={{ p: 1.5, borderRadius: 0, borderTop: 1, borderColor: 'divider' }}>
              {error && (
                <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
                  {error}
                </Alert>
              )}
              {showPluginInfoBanner && pluginsEnabled && chatPluginsSummaryText && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  Read-only plugins available in chat: {chatPluginsSummaryText}
                </Alert>
              )}

              <InputBar {...inputBarProps} />
            </Paper>

            {pluginsEnabled && (
              <PluginPickerDialog {...pluginPickerDialogProps} />
            )}

            {pluginsEnabled && pluginExecutionModalProps.plugin && (
              <PluginExecutionModal {...pluginExecutionModalProps} />
            )}

            <EnsembleModeDialog {...ensembleDialogProps} />
          </>
        ) : (
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              textAlign: 'center',
              p: 4,
            }}
          >
            <BotIcon sx={{ fontSize: 80, color: 'primary.main', mb: 2 }} />
            <Typography variant="h4" gutterBottom>
              {`Welcome to ${appDisplayName || ''}`}
            </Typography>
            <Typography variant="body1" color="text.secondary" sx={{ mb: 4, maxWidth: 500 }}>
              Start a new chat, select a model configuration in the top right and begin chatting with AI.
              Your conversations will be saved and you can switch between them anytime.
            </Typography>
            {getSelectedConfig() && (
              <Box sx={{ textAlign: 'center' }}>
                <Button
                  variant="contained"
                  size="large"
                  startIcon={<AddIcon />}
                  onClick={handleCreateConversation}
                  disabled={createConversationButtonDisabled}
                >
                  Start New Chat
                </Button>
              </Box>
            )}
          </Box>
        )}
      </Box>
    </Box>

    <RenameConversationDialog {...renameDialogProps} />

    <DeleteConversationDialog {...deleteDialogProps} />

    <ChatSettingsDialog {...settingsDialogProps} />
  </>
);

export default ModernChatView;
