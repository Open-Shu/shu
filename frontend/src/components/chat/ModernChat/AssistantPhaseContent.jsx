import React from 'react';
import MessageContent from './MessageContent';
import ThinkingIndicator from './ThinkingIndicator';
import StreamingFeather from './StreamingFeather';
import { PLACEHOLDER_THINKING } from './utils/chatConfig';

// Renders the body of an assistant bubble. Hard-cuts between the
// thinking and streaming phases — a crossfade was tried earlier but
// stranded the thinking feather visually while the first tokens
// arrived, exposing the bubble height delta instead of masking it.
const AssistantPhaseContent = React.memo(function AssistantPhaseContent({
  variant,
  hasReasoning,
  theme,
  isDarkMode,
  userBubbleText,
  assistantLinkColor,
  parseDocumentHref,
  onOpenDocument,
  attachmentChipStyles,
}) {
  const isThinkingPhase = Boolean(variant.isStreaming) && variant.content === PLACEHOLDER_THINKING && !hasReasoning;

  if (isThinkingPhase) {
    return <ThinkingIndicator message={variant} />;
  }

  return (
    <>
      <MessageContent
        message={variant}
        theme={theme}
        isDarkMode={isDarkMode}
        userBubbleText={userBubbleText}
        assistantLinkColor={assistantLinkColor}
        parseDocumentHref={parseDocumentHref}
        onOpenDocument={onOpenDocument}
        attachmentChipStyles={attachmentChipStyles}
      />
      <StreamingFeather isStreaming={Boolean(variant.isStreaming)} />
    </>
  );
});

export default AssistantPhaseContent;
