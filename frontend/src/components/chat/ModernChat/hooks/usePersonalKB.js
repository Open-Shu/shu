import { useCallback, useEffect, useRef, useState } from 'react';
import { extractDataFromResponse, extractItemsFromResponse, knowledgeBaseAPI } from '../../../../services/api';
import { log } from '../../../../utils/log';

// The Personal Knowledge KB is identified by a fixed name convention.
// One per user; auto-provisioned on first upload from chat.
export const PERSONAL_KB_NAME = 'Personal Knowledge';

const findPersonalKB = (kbs) => kbs.find((kb) => kb.name === PERSONAL_KB_NAME) || null;

/**
 * Manage the user's Personal Knowledge KB and the unified upload code path
 * shared across drag/drop, clipboard paste, and the Choose-files file picker.
 */
const usePersonalKB = () => {
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
    const promise = (async () => {
      const response = await knowledgeBaseAPI.create({ name: PERSONAL_KB_NAME });
      const created = extractDataFromResponse(response);
      setPersonalKB(created);
      return created;
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
