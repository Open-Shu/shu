import { useCallback, useEffect, useRef, useState } from 'react';
import { extractDataFromResponse, extractItemsFromResponse, knowledgeBaseAPI } from '../../../../services/api';
import { useAuth } from '../../../../hooks/useAuth';
import { log } from '../../../../utils/log';
import { resolveUserId } from '../../../../utils/userHelpers';

// Match BOTH the personal flag AND ownership. Admin users see every KB in the
// system via the list endpoint (default-allow filtering), so a bare
// kb.is_personal lookup would return whichever personal KB sorted first —
// typically another user's. That would then auto-attach to the admin's chat
// and silently inject someone else's documents into the LLM context.
const findPersonalKB = (kbs, userId) => {
  if (!userId) {
    return null;
  }
  return kbs.find((kb) => kb.is_personal === true && String(kb.owner_id) === String(userId)) || null;
};

/**
 * Manage the user's Personal Knowledge KB and the unified upload code path
 * shared across drag/drop, clipboard paste, and the Choose-files file picker.
 */
const usePersonalKB = () => {
  const { user } = useAuth();
  const [personalKB, setPersonalKB] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [errors, setErrors] = useState([]);

  // Avoid concurrent ensure() calls racing to create two KBs.
  const ensurePromiseRef = useRef(null);

  // /auth/me serializes the user via User.to_dict() which exposes the id as
  // `user_id`, not `id`. Use the canonical resolveUserId() helper so we don't
  // silently fall through to undefined and skip the ownership match.
  const userId = resolveUserId(user);

  const fetchPersonalKB = useCallback(async () => {
    setLoading(true);
    try {
      const response = await knowledgeBaseAPI.list({ limit: 100 });
      const items = extractItemsFromResponse(response);
      setPersonalKB(findPersonalKB(items, userId));
    } catch (err) {
      log.error('usePersonalKB: failed to fetch knowledge bases', err);
      // Soft-fail: leave personalKB null. Empty state pulses; first upload will create it.
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    fetchPersonalKB();
  }, [fetchPersonalKB]);

  const ensurePersonalKB = useCallback(async () => {
    if (personalKB) {
      return personalKB;
    }
    if (ensurePromiseRef.current) {
      return ensurePromiseRef.current;
    }
    // Server-side idempotent ensure. The display name is derived from the
    // user's identity by the backend (mirrors the precedence we used to do
    // here client-side); no body needed.
    const promise = (async () => {
      const response = await knowledgeBaseAPI.ensurePersonal();
      const kb = extractDataFromResponse(response);
      setPersonalKB(kb);
      return kb;
    })().finally(() => {
      ensurePromiseRef.current = null;
    });
    ensurePromiseRef.current = promise;
    return promise;
  }, [personalKB]);

  /**
   * Upload an array of File objects to the user's Personal Knowledge KB.
   * Auto-provisions the KB on first call. Returns the per-file results array
   * from the backend.
   *
   * Errors are tracked by filename so retryFile() can re-upload the same File.
   */
  const uploadFiles = useCallback(
    async (files) => {
      if (!files || files.length === 0) {
        return [];
      }
      setUploading(true);
      try {
        const kb = await ensurePersonalKB();
        const response = await knowledgeBaseAPI.uploadDocuments(kb.id, files);
        const data = extractDataFromResponse(response) || {};

        // Backend currently returns { results: [...] }, but earlier iterations
        // returned a bare array. Accept either shape; warn loudly on anything
        // else so a future API change surfaces in logs instead of silently
        // looking like an empty success.
        let results;
        if (Array.isArray(data)) {
          results = data;
        } else if (Array.isArray(data?.results)) {
          results = data.results;
        } else {
          log.warn('usePersonalKB: unexpected upload response shape', data);
          results = [];
        }

        const failedByName = new Map(results.filter((r) => !r.success).map((r) => [r.filename, r.error]));

        const newErrors = files
          .filter((file) => failedByName.has(file.name))
          .map((file) => ({
            filename: file.name,
            message: failedByName.get(file.name),
            file,
          }));

        const uploadedNames = new Set(files.map((f) => f.name));
        setErrors((prev) => [...prev.filter((e) => !uploadedNames.has(e.filename)), ...newErrors]);

        // Refresh doc count so brain icon badge updates.
        await fetchPersonalKB();
        return results;
      } catch (err) {
        log.error('usePersonalKB: upload failed', err);
        const message = err?.response?.data?.error?.message || 'Upload failed';
        const newErrors = files.map((file) => ({
          filename: file.name,
          message,
          file,
        }));
        const uploadedNames = new Set(files.map((f) => f.name));
        setErrors((prev) => [...prev.filter((e) => !uploadedNames.has(e.filename)), ...newErrors]);
        return [];
      } finally {
        setUploading(false);
      }
    },
    [ensurePersonalKB, fetchPersonalKB]
  );

  const retryFile = useCallback(
    async (filename) => {
      const error = errors.find((e) => e.filename === filename);
      if (!error || !error.file) {
        return [];
      }
      return uploadFiles([error.file]);
    },
    [errors, uploadFiles]
  );

  const dismissError = useCallback((filename) => {
    setErrors((prev) => prev.filter((e) => e.filename !== filename));
  }, []);

  return {
    personalKB,
    loading,
    uploading,
    errors,
    uploadFiles,
    retryFile,
    dismissError,
    refetch: fetchPersonalKB,
  };
};

export default usePersonalKB;
