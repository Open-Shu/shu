import React from 'react';
import {
  Box,
  Paper,
  Alert,
  Drawer,
  useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';

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
import ExperienceDashboard from '../../ExperienceDashboard';

const SIDEBAR_WIDTH = 300;

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
  mobileSidebarOpen,
  onCloseMobileSidebar,
  onToggleMobileSidebar,
}) => {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));

  const sidebarContent = <ConversationSidebar {...conversationSidebarProps} isMobile={isMobile} />;

  return (
    <>
      <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
        {/* Desktop sidebar - always visible */}
        {!isMobile && (
          <Box sx={{ width: SIDEBAR_WIDTH, flexShrink: 0 }}>
            {sidebarContent}
          </Box>
        )}

        {/* Mobile sidebar - drawer */}
        {isMobile && (
          <Drawer
            variant="temporary"
            open={mobileSidebarOpen}
            onClose={onCloseMobileSidebar}
            ModalProps={{ keepMounted: true }}
            sx={{
              '& .MuiDrawer-paper': {
                width: SIDEBAR_WIDTH,
                boxSizing: 'border-box',
              },
            }}
          >
            {sidebarContent}
          </Drawer>
        )}

        <Box sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {selectedConversation ? (
            <>
              <ChatHeader
                {...headerProps}
                isMobile={isMobile}
              />

              <AutomationMenu {...automationMenuProps} />

              <MessageList
                ref={messageListRef}
                key={selectedConversation?.id || 'no-conversation'}
                {...messageListProps}
              />

              <PluginRunPanel {...pluginRunPanelProps} />

              <DocumentPreview {...documentPreviewProps} />

              <Paper sx={{ p: { xs: 1, sm: 1.5 }, borderRadius: 0, borderTop: 1, borderColor: 'divider' }}>
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

                <InputBar {...inputBarProps} isMobile={isMobile} />
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
            <ExperienceDashboard
              onCreateConversation={handleCreateConversation}
              createConversationDisabled={createConversationButtonDisabled || !getSelectedConfig()}
              onStartChat={(experience) => {
                handleCreateConversation(experience);
              }}
            />
          )}
        </Box>
      </Box>

      <RenameConversationDialog {...renameDialogProps} />

      <DeleteConversationDialog {...deleteDialogProps} />

      <ChatSettingsDialog {...settingsDialogProps} />
    </>
  );
};

export default ModernChatView;
