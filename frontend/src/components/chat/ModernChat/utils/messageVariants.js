export const buildMessageVariants = (messages, variantSelection) => {
  const allMessages = Array.isArray(messages) ? messages : [];
  const variantGroupsMap = new Map();

  for (const message of allMessages) {
    if (message.role !== 'assistant') {
      continue;
    }
    const parentId = message.parent_message_id || message.id;
    if (!variantGroupsMap.has(parentId)) {
      variantGroupsMap.set(parentId, []);
    }
    variantGroupsMap.get(parentId).push(message);
  }

  for (const variants of variantGroupsMap.values()) {
    variants.sort((a, b) => {
      const aIndex = typeof a.variant_index === 'number' ? a.variant_index : null;
      const bIndex = typeof b.variant_index === 'number' ? b.variant_index : null;
      if (aIndex !== null && bIndex !== null) {
        return aIndex - bIndex;
      }
      return new Date(a.created_at) - new Date(b.created_at);
    });
  }

  const encounteredParents = new Set();
  const visibleMessages = [];

  for (const message of allMessages) {
    if (message.role !== 'assistant') {
      visibleMessages.push(message);
      continue;
    }

    const parentId = message.parent_message_id || message.id;
    if (encounteredParents.has(parentId)) {
      continue;
    }
    encounteredParents.add(parentId);

    const variants = variantGroupsMap.get(parentId) || [message];
    const selectedIndex =
      variantSelection && variantSelection[parentId] !== undefined
        ? variantSelection[parentId]
        : variants.length - 1;
    const clampedIndex = Math.max(0, Math.min(variants.length - 1, selectedIndex));
    visibleMessages.push(variants[clampedIndex]);
  }

  const variantGroups = {};
  for (const [parentId, group] of variantGroupsMap.entries()) {
    variantGroups[parentId] = group;
  }

  return { visibleMessages, variantGroups };
};

export const buildStreamingParentIds = (messages) => {
  const ids = new Set();
  const allMessages = Array.isArray(messages) ? messages : [];
  for (const message of allMessages) {
    if (message.role === 'assistant' && message.isStreaming) {
      const parentId = message.parent_message_id || message.id;
      const suppressed = Boolean(message.suppressSideBySide);
      if (!suppressed) {
        ids.add(parentId);
      }
      try {
        if (localStorage.getItem('chat_debug') === 'sidebyside') {
          // eslint-disable-next-line no-console
          console.debug('[SideBySide] streaming_parent_scan', {
            messageId: message.id,
            parentId,
            suppressed,
          });
        }
      } catch (_) {}
    }
  }
  return ids;
};

export const formatMessageTimestamp = (timestamp) => new Date(timestamp).toLocaleTimeString();
