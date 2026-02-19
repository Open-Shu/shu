import React, { forwardRef, useImperativeHandle, useMemo, useRef, useCallback } from 'react';
import { Box, Skeleton } from '@mui/material';
import MessageItem from './MessageItem';
import { CHAT_SCROLL_TOP_THRESHOLD, CHAT_SCROLL_BOTTOM_THRESHOLD, CHAT_WINDOW_SIZE } from './utils/chatConfig';

const MessageList = React.memo(
  forwardRef(function MessageList(
    {
      messages,
      loading,
      user,
      theme,
      chatStyles,
      attachmentChipStyles,
      variantGroups,
      variantSelection,
      sideBySideParents = new Set(),
      onToggleSideBySide,
      onVariantChange,
      onRegenerate,
      onCopy,
      isVariantGroupStreaming,
      parseDocumentHref,
      onOpenDocument,
      fallbackModelConfig,
      regenerationRequests,
      onLoadOlder,
      onRevealOlderInMemory,
      onRevealNewerInMemory,
      onBottomStateChange,
      onUserInteract,
      hasMore = false,
      isLoadingOlder = false,
      baseIndex = 0,
      totalCount,
      onToggleReasoning,
    },
    ref
  ) {
    const items = useMemo(() => (Array.isArray(messages) ? messages : []), [messages]);
    const scrollRef = useRef(null);
    const lastBottomStateRef = useRef(true);
    const topLoadArmedRef = useRef(false);
    const bottomLoadArmedRef = useRef(false);

    useImperativeHandle(
      ref,
      () => ({
        scrollToBottom: (behavior = 'auto') => {
          const el = scrollRef.current;
          if (!el) {
            return;
          }
          if (behavior === 'smooth') {
            el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
          } else {
            el.scrollTop = el.scrollHeight;
          }
        },
        captureScrollSnapshot: () => {
          const el = scrollRef.current;
          if (!el) {
            return null;
          }
          return {
            scrollHeight: el.scrollHeight,
            scrollTop: el.scrollTop,
          };
        },
        restoreScrollSnapshot: (snapshot) => {
          const el = scrollRef.current;
          if (!el || !snapshot) {
            return;
          }
          const delta = el.scrollHeight - snapshot.scrollHeight;
          el.scrollTop = snapshot.scrollTop + delta;
        },
        scrollToMessage: (messageId, { align = 'start', behavior = 'auto' } = {}) => {
          const container = scrollRef.current;
          if (!container || !messageId) {
            return;
          }
          const target = container.querySelector(`#msg-${messageId}`);
          if (!target) {
            return;
          }
          target.scrollIntoView({ behavior, block: align });
        },
      }),
      []
    );

    const handleScroll = useCallback(
      (event) => {
        const el = event.currentTarget;
        const remaining = el.scrollHeight - el.scrollTop - el.clientHeight;
        const atBottomRaw = remaining <= CHAT_SCROLL_BOTTOM_THRESHOLD;

        // Determine if the rendered end is the true end of the conversation
        const total = typeof totalCount === 'number' ? totalCount : baseIndex + items.length;
        // Compare using the non-overscanned window end so we don't prematurely treat overscan as "end"
        const isAtWindowEnd = baseIndex + CHAT_WINDOW_SIZE >= total;
        const atConversationBottom = atBottomRaw && isAtWindowEnd;

        if (atConversationBottom !== lastBottomStateRef.current) {
          lastBottomStateRef.current = atConversationBottom;
          onBottomStateChange?.(atConversationBottom);
        }

        if (!atConversationBottom) {
          onUserInteract?.();
        }

        const atTop = el.scrollTop <= CHAT_SCROLL_TOP_THRESHOLD;
        if (atTop) {
          if (hasMore && !isLoadingOlder && typeof onLoadOlder === 'function') {
            if (!topLoadArmedRef.current) {
              topLoadArmedRef.current = true;
              onLoadOlder();
            }
          } else if (baseIndex > 0 && typeof onRevealOlderInMemory === 'function') {
            if (!topLoadArmedRef.current) {
              topLoadArmedRef.current = true;
              onRevealOlderInMemory();
            }
          }
        } else if (el.scrollTop > CHAT_SCROLL_TOP_THRESHOLD) {
          topLoadArmedRef.current = false;
        }

        // Reveal newer-in-memory when we hit the bottom of the rendered chunk but not the true bottom
        if (atBottomRaw && !atConversationBottom && typeof onRevealNewerInMemory === 'function') {
          if (!bottomLoadArmedRef.current) {
            bottomLoadArmedRef.current = true;
            onRevealNewerInMemory();
          }
        } else if (!atBottomRaw) {
          bottomLoadArmedRef.current = false;
        }
      },
      [
        hasMore,
        isLoadingOlder,
        onBottomStateChange,
        onLoadOlder,
        onUserInteract,
        baseIndex,
        onRevealOlderInMemory,
        onRevealNewerInMemory,
        items.length,
        totalCount,
      ]
    );

    return (
      <Box sx={{ flexGrow: 1, display: 'flex', minHeight: 0 }}>
        {loading ? (
          <Box sx={{ flexGrow: 1, p: 2, overflow: 'auto' }}>
            {[1, 2, 3].map((i) => (
              <Box key={i} sx={{ mb: 2 }}>
                <Skeleton variant="circular" width={40} height={40} />
                <Skeleton variant="rectangular" height={60} sx={{ mt: 1 }} />
              </Box>
            ))}
          </Box>
        ) : (
          <Box
            ref={scrollRef}
            sx={{
              flexGrow: 1,
              overflowY: 'auto',
              overflowX: 'hidden',
              display: 'flex',
              flexDirection: 'column',
              py: 0,
              px: 1.5,
              pb: 2,
            }}
            onScroll={handleScroll}
            onWheel={() => onUserInteract?.()}
            onTouchMove={() => onUserInteract?.()}
          >
            <Box sx={{ height: 24, flexShrink: 0 }} />
            {items.map((message, index) => {
              const globalIndex = baseIndex + index;
              const isLastGlobal =
                typeof totalCount === 'number' ? globalIndex === totalCount - 1 : index === items.length - 1;
              const parentId = message.parent_message_id || message.id;
              const isSideBySide = sideBySideParents?.has?.(parentId);
              return (
                <MessageItem
                  key={message.id}
                  message={message}
                  isLast={isLastGlobal}
                  user={user}
                  theme={theme}
                  chatStyles={chatStyles}
                  attachmentChipStyles={attachmentChipStyles}
                  variantGroups={variantGroups}
                  variantSelection={variantSelection}
                  onVariantChange={onVariantChange}
                  onRegenerate={onRegenerate}
                  onCopy={onCopy}
                  isVariantGroupStreaming={isVariantGroupStreaming}
                  parseDocumentHref={parseDocumentHref}
                  onOpenDocument={onOpenDocument}
                  fallbackModelConfig={fallbackModelConfig}
                  regenerationRequests={regenerationRequests}
                  isSideBySide={!!isSideBySide}
                  onToggleSideBySide={onToggleSideBySide}
                  onToggleReasoning={onToggleReasoning}
                />
              );
            })}
          </Box>
        )}
      </Box>
    );
  })
);

export default MessageList;
