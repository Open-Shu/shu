/**
 * Resolve the canonical user ID from a user object.
 *
 * Backend responses may carry the identifier as `user_id` or `id` depending
 * on the serialisation context.  This helper normalises access so callers
 * don't need to check both.
 *
 * @param {object|null|undefined} user
 * @returns {string} The user ID, or an empty string when unavailable.
 */
export const resolveUserId = (user) => {
  if (!user) {
    return '';
  }
  return user.user_id || user.id || '';
};
