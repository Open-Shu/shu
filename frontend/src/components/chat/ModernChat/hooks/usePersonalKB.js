import { useCallback, useEffect, useRef, useState } from 'react';
import { extractDataFromResponse, extractItemsFromResponse, knowledgeBaseAPI } from '../../../../services/api';
import { useAuth } from '../../../../hooks/useAuth';
import { log } from '../../../../utils/log';

const FALLBACK_PERSONAL_KB_NAME = 'Personal Knowledge';

/**
 * Resolve the display name for a user's Personal Knowledge KB.
 *
 * Precedence (always prefers something identifying so admins viewing the
 * full KB list can tell whose is whose):
 *   1. `${firstName}'s Knowledge` — derived from the first whitespace-delimited
 *      token of `user.name`.
 *   2. `${emailLocalPart}'s Knowledge` — even if generic-looking ("user42"),
 *      because admins still need to identify the owner.
 *   3. "Personal Knowledge" — only when neither name nor email is present.
 */
export const resolvePersonalKBName = (user) => {
  // Coerce to String() so unexpected non-string inputs (e.g., numeric names
  // from a malformed user object) don't blow up on `.trim()`.
  const name = String(user?.name ?? '').trim();
  if (name) {
    const firstName = name.split(/\s+/)[0];
    if (firstName) {
      return `${firstName}'s Knowledge`;
    }
  }
  const email = String(user?.email ?? '').trim();
  if (email && email.includes('@')) {
    const localPart = email.split('@')[0].trim();
    if (localPart) {
      return `${localPart}'s Knowledge`;
    }
  }
  return FALLBACK_PERSONAL_KB_NAME;
};

const findPersonalKB = (kbs) => kbs.find((kb) => kb.is_personal === true) || null;

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

  const fetchPersonalKB = useCallback(async () => {
    setLoading(true);
    try {
      const response = await knowledgeBaseAPI.list({ limit: 100 });
      const items = extractItemsFromResponse(response);
      setPersonalKB(findPersonalKB(items));
    } catch (err) {
      log.error('usePersonalKB: failed to fetch knowledge bases', err);
      // Soft-fail: leave personalKB null. Empty state pulses; first upload will create it.
    } finally {
      setLoading(false);
    }
  }, []);

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
    const name = resolvePersonalKBName(user);
    const promise = (async () => {
      const response = await knowledgeBaseAPI.create({ name, is_personal: true });
      const created = extractDataFromResponse(response);
      setPersonalKB(created);
      return created;
    })().finally(() => {
      ensurePromiseRef.current = null;
    });
    ensurePromiseRef.current = promise;
    return promise;
  }, [personalKB, user]);

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
        const results = Array.isArray(data) ? data : data.results || [];

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
