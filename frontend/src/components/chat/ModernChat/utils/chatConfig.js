// Centralized configuration for ModernChat UI and streaming
// Values can be overridden via environment variables at build time (Vite: VITE_*)

import { PLUGINS_ENABLED } from '../../../../config/featureFlags';

const parsePositiveInt = (envKey, fallback) => {
  const raw = import.meta.env[envKey];
  if (raw === undefined || raw === null || raw === '') {
    return fallback;
  }
  const n = parseInt(String(raw), 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
};

// Allow 0 for parameters where zero is meaningful (e.g., overscan, thresholds)
const parseNonNegativeInt = (envKey, fallback) => {
  const raw = import.meta.env[envKey];
  if (raw === undefined || raw === null || raw === '') {
    return fallback;
  }
  const n = parseInt(String(raw), 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
};

// Boolean parser for feature toggles
const parseBoolean = (envKey, fallback = false) => {
  const raw = import.meta.env[envKey];
  if (raw === undefined || raw === null || raw === '') {
    return fallback;
  }
  const val = String(raw).trim().toLowerCase();
  if (val === 'true') {
    return true;
  }
  if (val === 'false') {
    return false;
  }
  return fallback;
};

// Feature toggles
// Chat plugin calling requires the plugins feature to be enabled at the app level.
// When VITE_PLUGINS_ENABLED is false, chat plugins are forced off regardless of
// VITE_CHAT_PLUGINS_ENABLED to prevent UI showing plugin controls with no
// admin/subscriptions pages available.
export const CHAT_PLUGINS_ENABLED = parseBoolean('VITE_CHAT_PLUGINS_ENABLED', false) && PLUGINS_ENABLED;

// Windowing + scroll thresholds
export const CHAT_WINDOW_SIZE = parsePositiveInt('VITE_CHAT_WINDOW_SIZE', 15);
export const CHAT_OVERSCAN = parseNonNegativeInt('VITE_CHAT_OVERSCAN', 5);
export const CHAT_SCROLL_TOP_THRESHOLD = parseNonNegativeInt('VITE_CHAT_SCROLL_TOP_THRESHOLD_PX', 120);
export const CHAT_SCROLL_BOTTOM_THRESHOLD = parseNonNegativeInt('VITE_CHAT_SCROLL_BOTTOM_THRESHOLD_PX', 32);

// Paging + refresh sizes
export const CHAT_PAGE_SIZE = parsePositiveInt('VITE_CHAT_PAGE_SIZE', 50);
export const CONVERSATION_LIST_LIMIT = parsePositiveInt('VITE_CONVERSATION_LIST_LIMIT', 50);

// Summary search behavior (allow env overrides; keep sane defaults)
export const SUMMARY_SEARCH_DEBOUNCE_MS = parsePositiveInt('VITE_SUMMARY_SEARCH_DEBOUNCE_MS', 300);
export const DEFAULT_SUMMARY_SEARCH_MIN_TERM_LENGTH = parsePositiveInt('VITE_SUMMARY_SEARCH_MIN_TERM_LENGTH', 3);
export const DEFAULT_SUMMARY_SEARCH_MAX_TOKENS = parsePositiveInt('VITE_SUMMARY_SEARCH_MAX_TOKENS', 10);

// Long conversation warning
export const LONG_CONVERSATION_THRESHOLD = parsePositiveInt('VITE_LONG_CONVERSATION_THRESHOLD', 50);

// UI Strings / Storage keys
export const STORAGE_KEY_RAG_REWRITE_MODE = 'shu.chat.ragRewriteMode';
export const PLACEHOLDER_THINKING = 'Thinking…';
export const DEFAULT_NEW_CHAT_TITLE = 'New Chat';
