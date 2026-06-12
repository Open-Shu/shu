// Copy pools + freshness logic for the welcome personality layer (SHU-873),
// rendered by shared/WelcomePanel on the chat landing and new-chat empty state.
//
// Mirrors the thinkingPhrases.js pattern: copy is plain data so it can be
// edited without touching component logic. `pickFresh` adds cross-session
// de-duplication via a small localStorage ring buffer so the same lines don't
// repeat back-to-back across mounts/reloads, degrading to a plain random pick
// when storage is unavailable (private mode) — it never throws.

// Greeting headlines. Each entry pairs a `named` template (with a `{name}`
// slot) and an `anon` fallback for when no name resolves, so the same greeting
// "upgrades" smoothly from anonymous to named once the user object loads
// instead of flickering between two unrelated lines.
export const GREETINGS = [
  { named: 'Welcome back, {name}.', anon: 'Welcome back.' },
  { named: 'Ready when you are, {name}.', anon: 'Ready when you are.' },
  { named: 'Good to see you, {name}.', anon: 'Good to see you.' },
  { named: "What's on your mind, {name}?", anon: "What's on your mind?" },
  { named: "Let's dig in, {name}.", anon: "Let's dig in." },
  { named: 'Where should we start, {name}?', anon: 'Where should we start?' },
  { named: 'The floor is yours, {name}.', anon: 'The floor is yours.' },
  { named: 'Fresh page, {name}. What are we exploring?', anon: 'Fresh page. What are we exploring?' },
  { named: 'Good to have you, {name}.', anon: 'Good to have you here.' },
];

// Sub-line under the greeting — short, topical, name-free.
export const SUBLINES = [
  'Ask a question, draft something, or think out loud.',
  'I can search your knowledge, summarize, translate, and plenty more.',
  'Bring a document, a question, or a half-formed idea.',
  'Pick a model, attach your Personal Knowledge, and go.',
  'Every good answer starts with a question.',
  'No question is too small — or too big.',
  'Type below, or start with one of these.',
  'Your knowledge, your models, one conversation away.',
];

// Clickable starter chips. `label` is shown on the chip; `prompt` is the text
// seeded into the composer (the user edits before sending — chips never
// auto-send). Trailing space/colon invites the user to complete the thought.
export const STARTER_CHIPS = [
  { label: 'Summarize a document', prompt: 'Summarize the key points of this document: ' },
  { label: 'Draft an email', prompt: 'Help me draft a professional email about ' },
  { label: 'Explain a concept', prompt: 'Explain this concept in simple terms: ' },
  { label: 'Brainstorm ideas', prompt: 'Brainstorm some ideas for ' },
  { label: 'Ask your Personal KB', prompt: 'Based on my documents, what do you know about ' },
  { label: 'Plan a project', prompt: 'Help me plan a project to ' },
  { label: 'Review my writing', prompt: 'Review the following text and suggest improvements:\n\n' },
  { label: 'Compare options', prompt: 'Help me compare the pros and cons of ' },
  { label: 'Turn notes into a plan', prompt: 'Turn these rough notes into an actionable plan:\n\n' },
  { label: 'Write some code', prompt: 'Write a small function that ' },
];

const RECENT_STORAGE_PREFIX = 'shu.welcome.recent.';

const readRecent = (key) => {
  try {
    const raw = window.localStorage.getItem(RECENT_STORAGE_PREFIX + key);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    // localStorage unavailable (private mode) or malformed JSON — treat as no history.
    return [];
  }
};

const writeRecent = (key, ids) => {
  try {
    window.localStorage.setItem(RECENT_STORAGE_PREFIX + key, JSON.stringify(ids));
  } catch (_) {
    // Best-effort only — a failed write just means the next pick has no history.
  }
};

const randomIndex = (length) => Math.floor(Math.random() * length);

/**
 * Pick `count` item(s) from `pool`, biased away from the most-recently-shown
 * items recorded under `key`, so copy stays fresh across mounts and reloads.
 *
 * - `identify(item)` maps an item to a stable id stored in the ring buffer
 *   (defaults to the item itself; pass a key extractor for object pools).
 * - When fewer than `count` un-shown items remain, the rotation restarts from
 *   the full pool so we never run dry.
 * - The ring buffer holds exactly the just-shown ids, so the next pick avoids
 *   repeating this selection back-to-back.
 *
 * Returns a single item when `count === 1` (or `null` for an empty pool), and
 * an array otherwise.
 */
export const pickFresh = (pool, key, { count = 1, identify = (item) => item } = {}) => {
  if (!Array.isArray(pool) || pool.length === 0) {
    return count === 1 ? null : [];
  }

  const recent = new Set(readRecent(key));
  let candidates = pool.filter((item) => !recent.has(identify(item)));
  if (candidates.length < count) {
    // Not enough fresh items left — start the rotation over from the full pool.
    candidates = [...pool];
  }

  const working = [...candidates];
  const chosen = [];
  const take = Math.min(count, working.length);
  for (let i = 0; i < take; i += 1) {
    const [item] = working.splice(randomIndex(working.length), 1);
    chosen.push(item);
  }

  writeRecent(key, chosen.map(identify));
  return count === 1 ? chosen[0] : chosen;
};

const MAX_NAME_LENGTH = 22;

const clampName = (value) => {
  const trimmed = value.trim();
  // Count/slice by code points (Array.from) so we never cut through a surrogate
  // pair and leave a stray replacement glyph in the greeting headline.
  const chars = Array.from(trimmed);
  if (chars.length <= MAX_NAME_LENGTH) {
    return trimmed;
  }
  // Drop a trailing space before the ellipsis so we don't render "Jonathan …".
  return `${chars.slice(0, MAX_NAME_LENGTH).join('').trimEnd()}…`;
};

/**
 * Derive a short, friendly greeting name for the user, entirely client-side
 * (the backend exposes only `user.name`). Resolution order: first token of
 * `name` → email local-part → '' (empty, so the caller uses an anonymous
 * greeting). Handles missing/empty/whitespace names and clamps very long names
 * so the headline never overflows on mobile or renders "Welcome back, .".
 */
export const getGreetingName = (user) => {
  if (!user || typeof user !== 'object') {
    return '';
  }

  const name = typeof user.name === 'string' ? user.name.trim() : '';
  if (name) {
    const firstToken = name.split(/\s+/)[0];
    if (firstToken) {
      return clampName(firstToken);
    }
  }

  const email = typeof user.email === 'string' ? user.email.trim() : '';
  if (email.includes('@')) {
    const localPart = email.split('@')[0];
    if (localPart) {
      return clampName(localPart);
    }
  }

  return '';
};
