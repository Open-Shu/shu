import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Button, Paper, Alert, Drawer, Stack, Typography, Fade, useMediaQuery } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Add as AddIcon, ChatBubbleOutline as ChatIcon, Dashboard as DashboardIcon } from '@mui/icons-material';

import { useFeatureEnabled } from '../../../config/featureFlags';
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
import KBPickerDialog from './KBPickerDialog';
import RenameConversationDialog from './RenameConversationDialog';
import DeleteConversationDialog from './DeleteConversationDialog';
import ChatSettingsDialog from './ChatSettingsDialog';
import LongConversationDialog from './LongConversationDialog';
import WelcomePanel from '../../shared/WelcomePanel';

const SIDEBAR_WIDTH = 300;
const WELCOME_FADE_MS = 320;

const ModernChatView = ({
  appDisplayName,
  welcomePanelProps,
  welcomePersonalityEnabled,
  showEmptyChatWelcome,
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
  kbPickerDialogProps,
  renameDialogProps,
  deleteDialogProps,
  settingsDialogProps,
  longConversationDialogProps,
  pluginsEnabled,
  getSelectedConfig,
  handleCreateConversation,
  createConversationButtonDisabled,
  mobileSidebarOpen,
  onCloseMobileSidebar,
}) => {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const navigate = useNavigate();
  const canExperiences = useFeatureEnabled('experiences');

  const sidebarContent = <ConversationSidebar {...conversationSidebarProps} isMobile={isMobile} />;

  return (
    <>
      <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
        {/* Desktop sidebar - always visible */}
        {!isMobile && <Box sx={{ width: SIDEBAR_WIDTH, flexShrink: 0 }}>{sidebarContent}</Box>}

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

        <Box
          sx={{
            flexGrow: 1,
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
          }}
        >
          {selectedConversation ? (
            <>
              <ChatHeader {...headerProps} isMobile={isMobile} />

              <AutomationMenu {...automationMenuProps} />

              <Box sx={{ position: 'relative', flexGrow: 1, minHeight: 0, display: 'flex' }}>
                <MessageList
                  ref={messageListRef}
                  key={selectedConversation?.id || 'no-conversation'}
                  {...messageListProps}
                />

                {welcomePersonalityEnabled && (
                  <Fade
                    in={Boolean(showEmptyChatWelcome)}
                    appear={false}
                    unmountOnExit
                    timeout={reduceMotion ? 0 : WELCOME_FADE_MS}
                  >
                    <Box
                      sx={{
                        position: 'absolute',
                        inset: 0,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        overflowY: 'auto',
                        p: 2,
                        bgcolor: 'background.default',
                      }}
                    >
                      <WelcomePanel variant="empty-chat" {...welcomePanelProps} />
                    </Box>
                  </Fade>
                )}
              </Box>

              <PluginRunPanel {...pluginRunPanelProps} />

              <DocumentPreview {...documentPreviewProps} />

              <Paper
                sx={{
                  p: { xs: 1, sm: 1.5 },
                  borderRadius: 0,
                  borderTop: 1,
                  borderColor: 'divider',
                }}
              >
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

                {longConversationDialogProps.open ? (
                  <LongConversationDialog {...longConversationDialogProps} />
                ) : (
                  <InputBar {...inputBarProps} isMobile={isMobile} />
                )}
              </Paper>

              {pluginsEnabled && <PluginPickerDialog {...pluginPickerDialogProps} />}

              {pluginsEnabled && pluginExecutionModalProps.plugin && (
                <PluginExecutionModal {...pluginExecutionModalProps} />
              )}

              <EnsembleModeDialog {...ensembleDialogProps} />
              <KBPickerDialog {...kbPickerDialogProps} />
            </>
          ) : welcomePersonalityEnabled ? (
            <Box
              sx={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                overflowY: 'auto',
                p: 4,
              }}
            >
              {/* Surface create failures (e.g. a landing starter-chip / New Chat
                  create that fails) — the composer Alert only exists in the
                  selected-conversation branch, so the landing screen needs its own. */}
              {error && (
                <Alert severity="error" sx={{ mb: 3, width: '100%', maxWidth: 720 }} onClose={() => setError(null)}>
                  {error}
                </Alert>
              )}
              <WelcomePanel variant="landing" {...welcomePanelProps} />
            </Box>
          ) : (
            /* Welcome screen (personality layer off) - shown when no conversation is selected */
            <Box
              sx={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                textAlign: 'center',
                p: 4,
              }}
            >
              <ChatIcon sx={{ fontSize: 80, color: 'grey.300', mb: 3 }} />
              <Typography variant="h4" sx={{ fontWeight: 600, mb: 1 }}>
                {appDisplayName}
              </Typography>
              <Typography variant="body1" color="text.secondary" sx={{ mb: 4, maxWidth: 400 }}>
                Select a conversation from the sidebar or start a new chat to begin.
              </Typography>
              <Stack direction="row" spacing={2}>
                <Button
                  variant="contained"
                  size="large"
                  startIcon={<AddIcon />}
                  onClick={handleCreateConversation}
                  disabled={createConversationButtonDisabled || !getSelectedConfig()}
                >
                  New Chat
                </Button>
                {canExperiences && (
                  <Button
                    variant="outlined"
                    size="large"
                    startIcon={<DashboardIcon />}
                    onClick={() => navigate('/dashboard')}
                  >
                    Dashboard
                  </Button>
                )}
              </Stack>
            </Box>
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
