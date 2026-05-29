export const buildUserPreferencesPayload = (preferences = {}) => ({
  memory_depth: preferences.memory_depth ?? 5,
  memory_similarity_threshold: preferences.memory_similarity_threshold ?? 0.6,
  theme: preferences.theme ?? 'light',
  language: preferences.language ?? 'en',
  timezone: preferences.timezone ?? 'UTC',
  font_family: preferences.font_family ?? null,
  font_size_scale: preferences.font_size_scale ?? null,
  advanced_settings: preferences.advanced_settings ?? {},
});
