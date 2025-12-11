// Shared helpers for working with the React Query cache shape used by chat
import { extractDataFromResponse } from '../../../../services/api';

export const getMessagesFromCache = (cache) => {
  const existing = extractDataFromResponse(cache);
  return Array.isArray(existing) ? existing : [];
};

export const rebuildCache = (cache, messages) => {
  if (cache && typeof cache === 'object' && 'data' in cache) {
    const outer = { ...cache };
    const inner = { ...(outer.data || {}) };
    if (inner.meta) {
      inner.meta = { ...inner.meta };
    }
    inner.data = messages;
    outer.data = inner;
    return outer;
  }
  return { data: { data: messages } };
};

export const mergeLatestMessagesIntoCache = (oldData, latestMessages, options = {}) => {
  const { placeholderId, removePlaceholders = true } = options || {};
  const existing = getMessagesFromCache(oldData);
  let base = Array.isArray(existing) ? existing : [];
  if (removePlaceholders) {
    base = base.filter((m) => !m?.isPlaceholder);
  }
  if (placeholderId) {
    base = base.filter((m) => m.id !== placeholderId);
  }
  const mergedMap = new Map();
  base.forEach((m) => mergedMap.set(m.id, m));
  if (Array.isArray(latestMessages)) {
    latestMessages.forEach((m) => mergedMap.set(m.id, m));
  }
  const merged = Array.from(mergedMap.values()).sort((a, b) => {
    const ta = a?.created_at ? new Date(a.created_at).getTime() : 0;
    const tb = b?.created_at ? new Date(b.created_at).getTime() : 0;
    return ta - tb;
  });
  return rebuildCache(oldData, merged);
};



