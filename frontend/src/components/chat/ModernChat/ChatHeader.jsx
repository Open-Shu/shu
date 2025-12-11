import React from 'react';
import {
  Box,
  Chip,
  CircularProgress,
  FormControl,
  IconButton,
  InputLabel,
  Menu,
  MenuItem,
  Paper,
  Select,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Description as DescriptionIcon,
  MoreVert as MoreVertIcon,
  Settings as SettingsIcon,
  Storage as KnowledgeBaseIcon,
  Lock as LockIcon,
  WarningAmber as WarningIcon,
} from '@mui/icons-material';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { titlePulse } from './styles';

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
}) {
  if (!conversation) {
    return null;
  }

  return (
    <>
      <Paper sx={{ p: 2, borderRadius: 0, borderBottom: 1, borderColor: 'divider' }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 2 }}>
          <Box sx={{ minWidth: 0 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography
                variant="h6"
                noWrap
                sx={isAutoRenaming ? { animation: `${titlePulse} 1.2s ease-in-out infinite` } : undefined}
              >
                {conversation.title}
              </Typography>
              {isAutoRenaming && (
                <CircularProgress size={14} sx={{ ml: 0.5 }} />
              )}
              <Tooltip title="View summary" arrow>
                <IconButton size="small" onClick={onOpenSummary} aria-label="View summary">
                  <DescriptionIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title="Menu" arrow>
                <IconButton size="small" onClick={onOpenAutomationMenu} aria-label="Menu">
                  <MoreVertIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              {sideCallWarning && (
                <Tooltip
                  title={sideCallWarning}
                  arrow
                >
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
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mt: 0.5 }}>
              {conversation.model_configuration?.knowledge_bases?.length > 0 && (
                <Chip
                  size="small"
                  icon={<KnowledgeBaseIcon />}
                  label={`${conversation.model_configuration.knowledge_bases.length} KB`}
                  color="secondary"
                  variant="outlined"
                />
              )}
              {Boolean(conversation?.meta?.title_locked) && (
                <Chip size="small" color="default" variant="outlined" icon={<LockIcon sx={{ fontSize: 14 }} />} label="Auto-rename locked" />
              )}
            </Box>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <FormControl size="small" sx={{ minWidth: 220 }}>
              <InputLabel>Model</InputLabel>
              <Select
                value={selectedModelConfig || ''}
                label="Model"
                onChange={onModelChange}
                disabled={disableModelSelect}
                displayEmpty
              >
                <MenuItem value="" disabled>
                  <Typography variant="body2" color="text.secondary">
                    Select a model
                  </Typography>
                </MenuItem>
                {availableModelConfigs.map((config) => (
                  <MenuItem key={config.id} value={config.id}>
                    <Box>
                      <Typography variant="body2" noWrap>{config.name}</Typography>
                      <Typography variant="caption" color="text.secondary" noWrap>
                        {(config.llm_provider?.name || 'Provider')} • {config.model_name}
                      </Typography>
                    </Box>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
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
          </Box>
        </Box>
      </Paper>

      <Menu
        anchorEl={summaryAnchorEl}
        open={Boolean(summaryAnchorEl)}
        onClose={onCloseSummary}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        PaperProps={{ sx: { maxWidth: 520, p: 1 } }}
      >
        <Box sx={{ maxWidth: 500, p: 1 }}>
          {conversation?.summary_text ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {conversation.summary_text}
            </ReactMarkdown>
          ) : (
            <Typography variant="body2" color="text.secondary">No summary yet</Typography>
          )}
        </Box>
      </Menu>
    </>
  );
});

export default ChatHeader;
