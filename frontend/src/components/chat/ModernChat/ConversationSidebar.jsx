import React from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Chip,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  Skeleton,
  Tooltip,
  Typography,
  TextField,
  InputAdornment,
} from "@mui/material";
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  Lock as LockIcon,
  Storage as KnowledgeBaseIcon,
  Dashboard as DashboardIcon,
  Star as StarIcon,
  StarBorder as StarBorderIcon,
} from "@mui/icons-material";
import { alpha, useTheme } from "@mui/material/styles";
import ClearIcon from "@mui/icons-material/Clear";
import MarkdownRenderer from "../../shared/MarkdownRenderer";

const ConversationSidebar = React.memo(function ConversationSidebar({
  conversations,
  loadingConversations,
  selectedConversationId,
  onSelectConversation,
  onCreateConversation,
  createConversationDisabled,
  showNoModelsNote,
  onRenameConversation,
  onDeleteConversation,
  onToggleFavorite,
  branding,
  chatStyles,
  searchValue,
  onSearchChange,
  searchFeedback,
  isMobile = false,
}) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === "dark";
  const navigate = useNavigate();

  return (
    <Paper
      sx={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        borderRadius: 0,
        borderRight: isMobile ? 0 : 1,
        borderColor: "divider",
      }}
    >
      <Box
        sx={{
          p: 2,
          borderBottom: 1,
          borderColor: "divider",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <Button
          fullWidth
          variant="outlined"
          startIcon={<DashboardIcon />}
          onClick={() => navigate("/dashboard")}
          sx={{ minHeight: 44 }}
        >
          Dashboard
        </Button>
        <Button
          fullWidth
          variant="contained"
          startIcon={<AddIcon />}
          onClick={onCreateConversation}
          disabled={createConversationDisabled}
          sx={{ minHeight: 44 }}
        >
          New Chat
        </Button>
        {showNoModelsNote && (
          <Typography variant="caption" color="text.secondary">
            No model configurations available.
          </Typography>
        )}
        <TextField
          value={searchValue || ""}
          onChange={(event) => onSearchChange?.(event.target.value)}
          size="small"
          label="Search summaries"
          placeholder="Enter keywords"
          variant="outlined"
          autoComplete="off"
          InputProps={{
            endAdornment:
              searchValue && searchValue.length > 0 ? (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    edge="end"
                    onClick={() => onSearchChange?.("")}
                    aria-label="Clear summary search"
                    sx={{ mr: -0.5 }}
                  >
                    <ClearIcon fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ) : null,
          }}
        />
        {searchFeedback ? (
          <Typography variant="caption" color="text.secondary">
            {searchFeedback}
          </Typography>
        ) : null}
      </Box>

      <Box sx={{ flexGrow: 1, overflow: "auto" }}>
        {loadingConversations ? (
          <Box sx={{ p: 2 }}>
            {[1, 2, 3].map((i) => (
              <Skeleton
                key={i}
                variant="rectangular"
                height={60}
                sx={{ mb: 1 }}
              />
            ))}
          </Box>
        ) : (
          <List dense>
            {conversations.map((conversation) => (
              <Box
                key={conversation.id}
                sx={{
                  position: "relative",
                  borderRadius: 1,
                  mb: 1,
                  "&:hover .conversation-action-button": {
                    opacity: 1,
                  },
                  // On mobile, always show action buttons for easier touch access
                  ...(isMobile && {
                    "& .conversation-action-button": {
                      opacity: 0.7,
                    },
                  }),
                }}
              >
                <Tooltip
                  title={
                    <Box sx={{ maxWidth: 360, p: 0.5 }}>
                      {conversation.summary_text ? (
                        <MarkdownRenderer
                          content={conversation.summary_text}
                          isDarkMode={isDarkMode}
                        />
                      ) : (
                        <Typography variant="caption" color="text.secondary">
                          No summary yet
                        </Typography>
                      )}
                    </Box>
                  }
                  placement="right"
                  arrow
                  disableHoverListener={!conversation.summary_text}
                  // Disable tooltip on mobile (not useful for touch)
                  disableTouchListener={isMobile}
                >
                  <ListItemButton
                    selected={selectedConversationId === conversation.id}
                    onClick={() => onSelectConversation(conversation)}
                    sx={{
                      borderRadius: 1,
                      pr: 1,
                      minHeight: 56,
                      border: `1px solid ${chatStyles.conversationBorderColor}`,
                      color:
                        selectedConversationId === conversation.id
                          ? chatStyles.conversationSelectedText
                          : theme.palette.text.primary,
                      "&.Mui-selected": {
                        bgcolor: chatStyles.conversationSelectedBg,
                        color: chatStyles.conversationSelectedText,
                        "&:hover": {
                          bgcolor: chatStyles.conversationSelectedBg,
                        },
                      },
                      "&:hover": {
                        bgcolor: chatStyles.conversationHoverBg,
                      },
                    }}
                  >
                    <ListItemText
                      primary={conversation.title}
                      secondary={
                        <Box
                          sx={{ display: "flex", alignItems: "center", gap: 1 }}
                        >
                          <Typography
                            variant="caption"
                            sx={{
                              color:
                                selectedConversationId === conversation.id
                                  ? alpha(
                                      chatStyles.conversationSelectedText,
                                      0.85,
                                    )
                                  : theme.palette.text.secondary,
                              fontWeight: 500,
                            }}
                          >
                            {conversation.model_configuration?.name}
                          </Typography>
                          {conversation.model_configuration?.knowledge_bases
                            ?.length > 0 && (
                            <KnowledgeBaseIcon
                              sx={{
                                fontSize: 12,
                                color:
                                  selectedConversationId === conversation.id
                                    ? theme.palette.secondary.main
                                    : theme.palette.primary.main,
                              }}
                            />
                          )}
                          {conversation.is_favorite && (
                            <StarIcon
                              sx={{
                                fontSize: 12,
                                color: theme.palette.warning.main,
                              }}
                            />
                          )}
                          {conversation?.meta?.title_locked && (
                            <Chip
                              size="small"
                              icon={<LockIcon sx={{ fontSize: 12 }} />}
                              label="Locked"
                              variant="outlined"
                            />
                          )}
                        </Box>
                      }
                      primaryTypographyProps={{
                        sx: {
                          color:
                            selectedConversationId === conversation.id
                              ? chatStyles.conversationSelectedText
                              : theme.palette.text.primary,
                          fontWeight: 600,
                        },
                      }}
                    />
                  </ListItemButton>
                </Tooltip>

                <IconButton
                  className="conversation-action-button"
                  aria-label={
                    conversation.is_favorite
                      ? "Remove from favorites"
                      : "Add to favorites"
                  }
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggleFavorite?.(conversation);
                  }}
                  sx={{
                    position: "absolute",
                    bottom: 4,
                    right: 72,
                    opacity: 0,
                    transition: "opacity 0.2s",
                    color: conversation.is_favorite
                      ? theme.palette.warning.main
                      : theme.palette.text.secondary,
                    "&:hover": {
                      bgcolor: alpha(
                        conversation.is_favorite
                          ? theme.palette.warning.main
                          : theme.palette.primary.main,
                        0.12,
                      ),
                    },
                    width: 28,
                    height: 28,
                  }}
                  size="small"
                >
                  {conversation.is_favorite ? (
                    <StarIcon sx={{ fontSize: 16 }} />
                  ) : (
                    <StarBorderIcon sx={{ fontSize: 16 }} />
                  )}
                </IconButton>

                <IconButton
                  className="conversation-action-button"
                  aria-label="Rename conversation"
                  onClick={(event) => {
                    event.stopPropagation();
                    onRenameConversation(conversation);
                  }}
                  sx={{
                    position: "absolute",
                    bottom: 4,
                    right: 40,
                    opacity: 0,
                    transition: "opacity 0.2s",
                    color: theme.palette.text.secondary,
                    "&:hover": {
                      bgcolor: alpha(theme.palette.primary.main, 0.12),
                    },
                    width: 28,
                    height: 28,
                  }}
                  size="small"
                >
                  <EditIcon sx={{ fontSize: 16 }} />
                </IconButton>

                <IconButton
                  className="conversation-action-button"
                  aria-label="Delete conversation"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDeleteConversation(conversation);
                  }}
                  sx={{
                    position: "absolute",
                    bottom: 4,
                    right: 8,
                    opacity: 0,
                    transition: "opacity 0.2s",
                    color: theme.palette.error.main,
                    "&:hover": {
                      bgcolor: alpha(theme.palette.error.main, 0.12),
                    },
                    width: 28,
                    height: 28,
                  }}
                  size="small"
                >
                  <DeleteIcon sx={{ fontSize: 16 }} />
                </IconButton>
              </Box>
            ))}
          </List>
        )}
      </Box>

      <Box
        sx={{
          mt: "auto",
          p: 2,
          backgroundColor: alpha(branding.primaryMain, 0.0),
          borderTop: `1px solid ${alpha(branding.primaryMain, 0.0)}`,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <img
          src={branding.logoUrl}
          alt={branding.appDisplayName}
          style={{
            height: "60px",
            width: "auto",
            maxWidth: "100%",
          }}
        />
      </Box>
    </Paper>
  );
});

export default ConversationSidebar;
