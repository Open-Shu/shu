export const getLatestUserMessageContent = (messages) => {
  if (!Array.isArray(messages)) return '';
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg?.role === 'user' && typeof msg.content === 'string' && msg.content.trim()) {
      return msg.content;
    }
  }
  return '';
};

export const buildRenamePayloadBase = (latestUserMessageContent, explicitFallback) => {
  const fallback = (explicitFallback && explicitFallback.trim()) || latestUserMessageContent;
  if (fallback && fallback.trim()) {
    return { fallback_user_message: fallback.trim() };
  }
  return {};
};
