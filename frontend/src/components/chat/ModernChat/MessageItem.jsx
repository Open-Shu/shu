import React, { useMemo } from 'react';
import { Avatar, Box, IconButton, Paper, Typography, Tooltip, CircularProgress, Button } from '@mui/material';
import {
  Person as UserIcon,
  SmartToy as BotIcon,
  Refresh as RefreshIcon,
  ContentCopy as ContentCopyIcon,
  NavigateBefore as NavigateBeforeIcon,
  NavigateNext as NavigateNextIcon,
  ViewColumn as SideBySideIcon,
} from '@mui/icons-material';
import MessageContent from './MessageContent';
import UserAvatar from '../../shared/UserAvatar.jsx';
import { formatMessageTimestamp } from './utils/messageVariants';
import { PLACEHOLDER_THINKING } from './utils/chatConfig';

const MessageItem = React.memo(function MessageItem({
  message,
  isLast = false,
  user,
  theme,
  chatStyles,
  attachmentChipStyles,
  variantGroups,
  variantSelection,
  onVariantChange,
  onRegenerate,
  onCopy,
  isVariantGroupStreaming,
  parseDocumentHref,
  onOpenDocument,
  fallbackModelConfig,
  regenerationRequests,
  isSideBySide = false,
  onToggleSideBySide,
  onToggleReasoning,
}) {
  const parentId = message.parent_message_id || message.id;
  const group = useMemo(() => variantGroups[parentId] || [message], [parentId, message, variantGroups]);
  const currentIndex = useMemo(() => {
    if (!group.length) {
      return 0;
    }
    const explicit = variantSelection[parentId];
    if (typeof explicit === 'number' && explicit >= 0 && explicit < group.length) {
      return explicit;
    }
    const fallback = group.findIndex((item) => item.id === message.id);
    return fallback === -1 ? group.length - 1 : fallback;
  }, [group, message.id, parentId, variantSelection]);

  const handlePrev = () => {
    if (currentIndex <= 0) {
      return;
    }
    onVariantChange(parentId, currentIndex - 1);
  };

  const handleNext = () => {
    if (currentIndex >= group.length - 1) {
      return;
    }
    onVariantChange(parentId, currentIndex + 1);
  };

  const pendingRegenerationForGroup = useMemo(() => {
    if (!regenerationRequests || typeof regenerationRequests.forEach !== 'function') {
      return false;
    }
    let pending = false;
    regenerationRequests.forEach((entry) => {
      if (entry?.parentId === parentId && entry?.status === 'pending') {
        pending = true;
      }
    });
    return pending;
  }, [regenerationRequests, parentId]);

  const disableRegenerate = message.isStreaming || isVariantGroupStreaming(parentId) || pendingRegenerationForGroup;

  const isUser = message.role === 'user';

  const avatarNode = isUser ? (
    <UserAvatar
      user={user}
      size={36}
      sx={{
        bgcolor: chatStyles.userBubbleBg,
        color: chatStyles.userBubbleText,
        flexShrink: 0,
      }}
      fallbackChar={<UserIcon fontSize="small" />}
    />
  ) : (
    <Avatar
      sx={{
        bgcolor: theme.palette.secondary.main,
        color: theme.palette.secondary.contrastText,
        width: 36,
        height: 36,
        flexShrink: 0,
      }}
    >
      <BotIcon />
    </Avatar>
  );

  const containerDirection = isUser ? 'row-reverse' : 'row';
  const containerJustify = isUser ? 'flex-end' : 'flex-start';
  const timestampColor = isUser ? chatStyles.userBubbleText : theme.palette.text.secondary;

  const extractModelInfo = (msg) => {
    const rawContent = typeof msg?.content === 'string' ? msg.content : '';
    const hasRenderableContent = rawContent.trim().length > 0 && rawContent.trim() !== PLACEHOLDER_THINKING.trim();
    if (msg?.role === 'assistant' && !hasRenderableContent) {
      return { name: null, tooltip: '' };
    }
    const snapshot =
      msg?.model_configuration ||
      (msg?.message_metadata && msg.message_metadata.model_configuration) ||
      (msg?.role === 'assistant' ? fallbackModelConfig : null);

    const name = snapshot?.name || snapshot?.display_name || snapshot?.model_name || snapshot?.id || null;

    let tooltip = '';
    if (snapshot) {
      const tooltipParts = [];
      if (snapshot.name) {
        tooltipParts.push(`Configuration: ${snapshot.name}`);
      }
      if (snapshot.display_name || snapshot.model_name) {
        tooltipParts.push(`Model: ${snapshot.display_name || snapshot.model_name}`);
      }
      if (snapshot.id) {
        tooltipParts.push(`ID: ${snapshot.id}`);
      }
      if (snapshot.provider?.name) {
        const providerType = snapshot.provider?.provider_type;
        tooltipParts.push(`Provider: ${snapshot.provider.name}${providerType ? ` (${providerType})` : ''}`);
      }
      if (tooltipParts.length === 0) {
        tooltipParts.push('Unknown configuration');
      }
      tooltip = tooltipParts.filter(Boolean).join(' • ');
    }

    return {
      name,
      tooltip,
    };
  };

  if (isUser) {
    const { name, tooltip } = extractModelInfo(message);
    const userBubbleSx = {
      p: 2,
      flexShrink: 1,
      width: 'fit-content',
      maxWidth: 'min(85%, calc(100% - 56px))',
      minWidth: 0,
      overflowWrap: 'anywhere',
      wordBreak: 'break-word',
      bgcolor: chatStyles.userBubbleBg,
      border: 'none',
      boxShadow: 'none',
    };

    return (
      <Box id={`msg-${message.id}`} sx={{ mb: isLast ? 0 : 2 }}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: containerJustify,
          }}
        >
          <Box
            sx={{
              display: 'flex',
              flexDirection: containerDirection,
              alignItems: 'flex-end',
              gap: 1.25,
              width: '100%',
              maxWidth: '100%',
              pr: 6,
            }}
          >
            {avatarNode}
            <Paper sx={userBubbleSx}>
              <MessageContent
                message={message}
                theme={theme}
                isDarkMode={chatStyles.isDarkMode}
                userBubbleText={chatStyles.userBubbleText}
                assistantLinkColor={chatStyles.assistantLinkColor}
                parseDocumentHref={parseDocumentHref}
                onOpenDocument={onOpenDocument}
                attachmentChipStyles={attachmentChipStyles}
              />
              <Typography
                variant="caption"
                sx={{
                  mt: 1,
                  display: 'block',
                  color: timestampColor,
                  fontWeight: 500,
                }}
              >
                {formatMessageTimestamp(message.created_at)}
                {name && (
                  <>
                    {' • '}
                    <Tooltip title={tooltip || name} arrow>
                      <Box
                        component="span"
                        sx={{
                          display: 'inline',
                          color: timestampColor,
                          fontWeight: 600,
                          cursor: 'default',
                          opacity: 0.85,
                        }}
                      >
                        {name}
                      </Box>
                    </Tooltip>
                  </>
                )}
              </Typography>
            </Paper>
          </Box>
        </Box>
      </Box>
    );
  }

  const getBubbleSx = (variant, isVariantPending) => {
    const base = {
      p: 2,
      flexShrink: isSideBySide ? 1 : 0,
      flexGrow: isSideBySide ? 1 : 0,
      flexBasis: isSideBySide ? { xs: '100%', sm: '48%', lg: '45%' } : 'auto',
      width: isSideBySide ? { xs: '100%', md: 'auto' } : 'fit-content',
      maxWidth: isSideBySide
        ? { xs: '100%', md: 'min(480px, 100%)', xl: 'min(620px, 100%)' }
        : 'min(85%, calc(100% - 56px))',
      minWidth: isSideBySide ? { xs: '100%', sm: 280, lg: 320 } : 0,
      overflowWrap: 'anywhere',
      wordBreak: 'break-word',
      bgcolor: variant.role === 'user' ? chatStyles.userBubbleBg : chatStyles.assistantBubbleBg,
      border: variant.role === 'user' ? 'none' : chatStyles.assistantBubbleBorder,
      boxShadow: 'none',
      cursor: variant.isStreaming || isVariantPending ? 'wait' : undefined,
      display: 'flex',
      flexDirection: 'column',
      gap: 1,
    };

    return base;
  };

  const variantsToRender = isSideBySide ? group : [group[currentIndex] || message];

  const shouldCollapseBottomMargin = isLast && message.role === 'assistant';

  return (
    <Box id={`msg-${message.id}`} sx={{ mb: shouldCollapseBottomMargin ? 0 : 2 }}>
      <Box
        sx={{
          display: 'flex',
          justifyContent: containerJustify,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            flexDirection: containerDirection,
            alignItems: 'flex-end',
            gap: 1.25,
            width: '100%',
            maxWidth: '100%',
            pr: 0,
          }}
        >
          {avatarNode}
          <Box
            sx={{
              display: 'flex',
              flexDirection: isSideBySide ? 'row' : 'column',
              alignItems: isSideBySide ? 'stretch' : 'flex-start',
              gap: isSideBySide ? 1.5 : 0,
              flexWrap: isSideBySide ? 'wrap' : 'nowrap',
              width: '100%',
              maxWidth: '100%',
              justifyContent: isSideBySide ? (isUser ? 'flex-end' : 'flex-start') : 'flex-start',
            }}
          >
            {variantsToRender.map((variant, idx) => {
              const variantRequestEntry =
                regenerationRequests && typeof regenerationRequests.get === 'function'
                  ? regenerationRequests.get(variant.id)
                  : null;
              const variantPending = Boolean(variantRequestEntry && variantRequestEntry.status === 'pending');
              const { name, tooltip } = extractModelInfo(variant);
              const showVariantLabel = isSideBySide && group.length > 1;
              const reasoningText = (variant.reasoning_stream || '').trim();
              const hasReasoning = reasoningText.length > 0;
              const reasoningCollapsed = Boolean(variant.reasoning_collapsed);
              return (
                <Paper key={variant.id} sx={getBubbleSx(variant, variantPending)}>
                  {showVariantLabel && (
                    <Typography variant="caption" sx={{ fontWeight: 600, opacity: 0.7 }}>
                      Variant {idx + 1}
                    </Typography>
                  )}
                  {hasReasoning && (
                    <Box
                      sx={{
                        mb: 1,
                        p: 1,
                        borderRadius: 1,
                        bgcolor: theme.palette.action.hover,
                        width: '100%',
                      }}
                    >
                      <Box
                        sx={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          mb: reasoningCollapsed ? 0 : 0.5,
                        }}
                      >
                        <Typography variant="caption" sx={{ fontWeight: 600, opacity: 0.8 }}>
                          Reasoning
                        </Typography>
                        <Button size="small" onClick={() => onToggleReasoning?.(variant.id, !reasoningCollapsed)}>
                          {reasoningCollapsed ? 'Show' : 'Hide'}
                        </Button>
                      </Box>
                      {!reasoningCollapsed && (
                        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', opacity: 0.85 }}>
                          {reasoningText}
                        </Typography>
                      )}
                    </Box>
                  )}
                  <MessageContent
                    message={variant}
                    theme={theme}
                    isDarkMode={chatStyles.isDarkMode}
                    userBubbleText={chatStyles.userBubbleText}
                    assistantLinkColor={chatStyles.assistantLinkColor}
                    parseDocumentHref={parseDocumentHref}
                    onOpenDocument={onOpenDocument}
                    attachmentChipStyles={attachmentChipStyles}
                  />
                  {variant.isStreaming && (
                    <Box
                      sx={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        ml: 1,
                      }}
                    >
                      <CircularProgress size={12} sx={{ color: theme.palette.secondary.main }} />
                    </Box>
                  )}
                  <Typography
                    variant="caption"
                    sx={{
                      mt: 0.5,
                      display: 'block',
                      color: timestampColor,
                      fontWeight: 500,
                    }}
                  >
                    {formatMessageTimestamp(variant.created_at)}
                    {name && (
                      <>
                        {' • '}
                        <Tooltip title={tooltip || name} arrow>
                          <Box
                            component="span"
                            sx={{
                              display: 'inline',
                              color: timestampColor,
                              fontWeight: 600,
                              cursor: 'default',
                              opacity: isUser ? 0.85 : 1,
                            }}
                          >
                            {name}
                          </Box>
                        </Tooltip>
                      </>
                    )}
                  </Typography>
                </Paper>
              );
            })}
          </Box>
        </Box>
      </Box>

      {message.role === 'assistant' && (
        <Box sx={{ mt: 0.5, pl: 7, display: 'flex', alignItems: 'center', gap: 1 }}>
          {group.length > 1 && !isSideBySide && (
            <>
              <Tooltip title="Previous variant">
                <span>
                  <IconButton
                    size="small"
                    onClick={handlePrev}
                    disabled={currentIndex === 0}
                    aria-label="Previous variant"
                  >
                    <NavigateBeforeIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
              <Typography variant="caption">
                {currentIndex + 1}/{group.length}
              </Typography>
              <Tooltip title="Next variant">
                <span>
                  <IconButton
                    size="small"
                    onClick={handleNext}
                    disabled={currentIndex === group.length - 1}
                    aria-label="Next variant"
                  >
                    <NavigateNextIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            </>
          )}
          {group.length > 1 && (
            <Tooltip title={isSideBySide ? 'Exit side-by-side view' : 'Show variants side-by-side'}>
              <span>
                <IconButton
                  size="small"
                  onClick={() => onToggleSideBySide?.(parentId)}
                  aria-label="Toggle side-by-side variants"
                  color={isSideBySide ? 'primary' : 'default'}
                  disabled={!onToggleSideBySide}
                >
                  <SideBySideIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          )}
          {group.length > 1 && isSideBySide && (
            <Typography variant="caption" sx={{ opacity: 0.7 }}>
              All {group.length} variants
            </Typography>
          )}
          <Tooltip title="Regenerate">
            <span>
              <IconButton
                size="small"
                disabled={disableRegenerate}
                onClick={() => !disableRegenerate && onRegenerate(message.id, parentId)}
                aria-label="Regenerate"
              >
                <RefreshIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Copy">
            <IconButton size="small" onClick={() => onCopy(message.content || '')} aria-label="Copy">
              <ContentCopyIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
      )}
    </Box>
  );
});

export default MessageItem;
