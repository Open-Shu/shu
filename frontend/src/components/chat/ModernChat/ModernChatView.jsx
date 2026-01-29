import React from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Paper,
  Alert,
  Drawer,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import {
  Add as AddIcon,
  ChatBubbleOutline as ChatIcon,
  Dashboard as DashboardIcon,
} from "@mui/icons-material";

import ConversationSidebar from "./ConversationSidebar";
import ChatHeader from "./ChatHeader";
import AutomationMenu from "./AutomationMenu";
import MessageList from "./MessageList";
import PluginRunPanel from "./PluginRunPanel";
import DocumentPreview from "../../DocumentPreview";
import InputBar from "./InputBar";
import PluginPickerDialog from "../../PluginPickerDialog";
import PluginExecutionModal from "../../PluginExecutionModal";
import EnsembleModeDialog from "./EnsembleModeDialog";
import RenameConversationDialog from "./RenameConversationDialog";
import DeleteConversationDialog from "./DeleteConversationDialog";
import ChatSettingsDialog from "./ChatSettingsDialog";

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
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));
  const navigate = useNavigate();

  const sidebarContent = (
    <ConversationSidebar {...conversationSidebarProps} isMobile={isMobile} />
  );

  return (
    <>
      <Box sx={{ display: "flex", height: "100%", overflow: "hidden" }}>
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
              "& .MuiDrawer-paper": {
                width: SIDEBAR_WIDTH,
                boxSizing: "border-box",
              },
            }}
          >
            {sidebarContent}
          </Drawer>
        )}

        <Box
          sx={{
            flexGrow: 1,
            display: "flex",
            flexDirection: "column",
            minWidth: 0,
          }}
        >
          {selectedConversation ? (
            <>
              <ChatHeader {...headerProps} isMobile={isMobile} />

              <AutomationMenu {...automationMenuProps} />

              <MessageList
                ref={messageListRef}
                key={selectedConversation?.id || "no-conversation"}
                {...messageListProps}
              />

              <PluginRunPanel {...pluginRunPanelProps} />

              <DocumentPreview {...documentPreviewProps} />

              <Paper
                sx={{
                  p: { xs: 1, sm: 1.5 },
                  borderRadius: 0,
                  borderTop: 1,
                  borderColor: "divider",
                }}
              >
                {error && (
                  <Alert
                    severity="error"
                    sx={{ mb: 2 }}
                    onClose={() => setError(null)}
                  >
                    {error}
                  </Alert>
                )}
                {showPluginInfoBanner &&
                  pluginsEnabled &&
                  chatPluginsSummaryText && (
                    <Alert severity="info" sx={{ mb: 2 }}>
                      Read-only plugins available in chat:{" "}
                      {chatPluginsSummaryText}
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
            /* Welcome screen - shown when no conversation is selected */
            <Box
              sx={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                textAlign: "center",
                p: 4,
              }}
            >
              <ChatIcon sx={{ fontSize: 80, color: "grey.300", mb: 3 }} />
              <Typography variant="h4" sx={{ fontWeight: 600, mb: 1 }}>
                {appDisplayName}
              </Typography>
              <Typography
                variant="body1"
                color="text.secondary"
                sx={{ mb: 4, maxWidth: 400 }}
              >
                Select a conversation from the sidebar or start a new chat to
                begin.
              </Typography>
              <Stack direction="row" spacing={2}>
                <Button
                  variant="contained"
                  size="large"
                  startIcon={<AddIcon />}
                  onClick={handleCreateConversation}
                  disabled={
                    createConversationButtonDisabled || !getSelectedConfig()
                  }
                >
                  New Chat
                </Button>
                <Button
                  variant="outlined"
                  size="large"
                  startIcon={<DashboardIcon />}
                  onClick={() => navigate("/dashboard")}
                >
                  Dashboard
                </Button>
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
