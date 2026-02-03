import React from "react";
import {
  Box,
  Chip,
  CircularProgress,
  IconButton,
  Menu,
  Paper,
  Tooltip,
  Typography,
} from "@mui/material";
import ModelConfigSelector from "./ModelConfigSelector";
import {
  Description as DescriptionIcon,
  MoreVert as MoreVertIcon,
  Settings as SettingsIcon,
  Storage as KnowledgeBaseIcon,
  Lock as LockIcon,
  WarningAmber as WarningIcon,
} from "@mui/icons-material";
import { useTheme } from "@mui/material/styles";
import { titlePulse } from "./styles";
import MarkdownRenderer from "../../shared/MarkdownRenderer";

const ChatHeader = React.memo(function ChatHeader({
  conversation,
  isAutoRenaming,
  onOpenSummary,
  summaryAnchorEl,
  onCloseSummary,
  onOpenAutomationMenu,
  availableModelConfigs,
  selectedModelConfig,
  onModelChange,
  disableModelSelect,
  onOpenSettings,
  sideCallWarning,
  isMobile = false,
}) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === "dark";

  if (!conversation) {
    return null;
  }

  return (
    <>
      <Paper
        sx={{
          p: { xs: 1, sm: 2 },
          borderRadius: 0,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: { xs: 1, sm: 2 },
          }}
        >
          <Box
            sx={{ minWidth: 0, display: "flex", alignItems: "center", gap: 1 }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <Typography
                  variant="h6"
                  noWrap
                  sx={{
                    ...(isAutoRenaming
                      ? { animation: `${titlePulse} 1.2s ease-in-out infinite` }
                      : undefined),
                    fontSize: { xs: "1rem", sm: "1.25rem" },
                  }}
                >
                  {conversation.title}
                </Typography>
                {isAutoRenaming && (
                  <CircularProgress size={14} sx={{ ml: 0.5 }} />
                )}
                {!isMobile && (
                  <>
                    <Tooltip title="View summary" arrow>
                      <IconButton
                        size="small"
                        onClick={onOpenSummary}
                        aria-label="View summary"
                      >
                        <DescriptionIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Menu" arrow>
                      <IconButton
                        size="small"
                        onClick={onOpenAutomationMenu}
                        aria-label="Menu"
                      >
                        <MoreVertIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </>
                )}
                {sideCallWarning && (
                  <Tooltip title={sideCallWarning} arrow>
                    <IconButton
                      size="small"
                      color="warning"
                      aria-label="Side-caller configuration warning"
                    >
                      <WarningIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                )}
              </Box>
              {!isMobile && (
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    flexWrap: "wrap",
                    mt: 0.5,
                  }}
                >
                  {conversation.model_configuration?.knowledge_bases?.length >
                    0 && (
                    <Chip
                      size="small"
                      icon={<KnowledgeBaseIcon />}
                      label={`${conversation.model_configuration.knowledge_bases.length} KB`}
                      color="secondary"
                      variant="outlined"
                    />
                  )}
                  {Boolean(conversation?.meta?.title_locked) && (
                    <Chip
                      size="small"
                      color="default"
                      variant="outlined"
                      icon={<LockIcon sx={{ fontSize: 14 }} />}
                      label="Auto-rename locked"
                    />
                  )}
                </Box>
              )}
            </Box>
          </Box>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: { xs: 0.5, sm: 1.5 },
            }}
          >
            {/* Mobile: show summary, settings, and menu buttons */}
            {isMobile ? (
              <>
                <Tooltip title="View summary" arrow>
                  <IconButton
                    size="small"
                    onClick={onOpenSummary}
                    aria-label="View summary"
                  >
                    <DescriptionIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Menu" arrow>
                  <IconButton
                    size="small"
                    onClick={onOpenAutomationMenu}
                    aria-label="Menu"
                  >
                    <MoreVertIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Chat settings" arrow>
                  <IconButton
                    size="small"
                    onClick={onOpenSettings}
                    aria-label="Chat settings"
                  >
                    <SettingsIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </>
            ) : (
              <>
                <ModelConfigSelector
                  availableModelConfigs={availableModelConfigs}
                  selectedModelConfig={selectedModelConfig}
                  onModelChange={onModelChange}
                  disabled={disableModelSelect}
                />
                <Tooltip title="Chat settings" arrow>
                  <IconButton
                    onClick={onOpenSettings}
                    color="default"
                    size="small"
                    aria-label="Chat settings"
                  >
                    <SettingsIcon />
                  </IconButton>
                </Tooltip>
              </>
            )}
          </Box>
        </Box>
      </Paper>

      <Menu
        anchorEl={summaryAnchorEl}
        open={Boolean(summaryAnchorEl)}
        onClose={onCloseSummary}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        PaperProps={{ sx: { maxWidth: 520, p: 1 } }}
      >
        <Box sx={{ maxWidth: 500, p: 1 }}>
          {conversation?.summary_text ? (
            <MarkdownRenderer
              content={conversation.summary_text}
              isDarkMode={isDarkMode}
            />
          ) : (
            <Typography variant="body2" color="text.secondary">
              No summary yet
            </Typography>
          )}
        </Box>
      </Menu>
    </>
  );
});

export default ChatHeader;
