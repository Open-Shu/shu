import { useCallback, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from 'react-query';
import { extractDataFromResponse, knowledgeBaseAPI } from '../../../../services/api';
import { useAuth } from '../../../../hooks/useAuth';
import { log } from '../../../../utils/log';

// React Query keys (react-query v3 — positional API). Documents are parameterized
// by KB id so switching/clearing the personal KB re-keys cleanly.
const PERSONAL_KB_KEY = ['personalKB'];
const personalDocsKey = (kbId) => ['personalKBDocuments', kbId];
// The KB picker's own query; must be invalidated on any personal-KB mutation so
// its attach-list and doc counts stay fresh.
const PICKER_KEY = ['knowledge-bases-for-chat'];

const DOC_PAGE_LIMIT = 50;
const POLL_INTERVAL_MS = 4000;
const STALE_MS = 10000;

// A document is still being processed (any non-terminal pipeline status). Drives
// the brain "indexing" signal and the self-stopping doc-list refetch (SHU-817 R7).
const TERMINAL_SUCCESS_STATUSES = new Set(['content_processed', 'rag_processed', 'profile_processed']);
const TERMINAL_FAILURE_STATUSES = new Set(['error']);
const isDocNonTerminal = (doc) => {
  const status = doc?.processing_status || 'pending';
  return !TERMINAL_SUCCESS_STATUSES.has(status) && !TERMINAL_FAILURE_STATUSES.has(status);
};

// Flatten the useInfiniteQuery pages (each is the {items,total,limit,offset}
// envelope body) into a single newest-first document array.
const flattenPages = (infiniteData) =>
  (infiniteData?.pages || []).flatMap((page) => (Array.isArray(page?.items) ? page.items : []));

// Stable per-file identity for the upload error list. Filename alone collides
// when two same-named files are dropped together, so retry/dismiss would act on
// the wrong row (SHU-817 R1).
let clientKeyCounter = 0;
const nextClientKey = (file) => {
  clientKeyCounter += 1;
  return `${file.name}:${file.size}:${file.lastModified}:${clientKeyCounter}`;
};

/**
 * Manage the user's Personal Knowledge KB, its documents, and the unified upload
 * code path shared across drag/drop, clipboard paste, and the Choose-files picker.
 *
 * The personal KB and its documents are React Query-backed so the popover, the
 * brain badge/indexing signal, and the KB picker stay consistent; mutations
 * invalidate the shared keys (SHU-817 F4).
 */
const usePersonalKB = () => {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  // `uploading` and `errors` are client-only UI state (not server state).
  // inFlightUploadsRef is a counter, not a bool, so overlapping batches (e.g. a
  // retry fired mid drag-drop) only flip `uploading` false when the LAST settles.
  const [uploading, setUploading] = useState(false);
  const [errors, setErrors] = useState([]); // Array<{ clientKey, filename, message, file }>
  const ensurePromiseRef = useRef(null);
  const inFlightUploadsRef = useRef(0);

  // Owner-scoped direct lookup — the server returns {data: <kb>|null}, so we
  // unwrap with extractDataFromResponse rather than the old list({limit:100}) +
  // client-side ownership filter, which could paginate the KB out for users who
  // see many KBs (SHU-817 R5).
  const personalKbQuery = useQuery(
    PERSONAL_KB_KEY,
    () => knowledgeBaseAPI.getPersonal().then(extractDataFromResponse),
    { enabled: !!user, staleTime: STALE_MS }
  );
  const personalKB = personalKbQuery.data ?? null;
  const kbId = personalKB?.id ?? null;

  // Lifted out of BrainPopover so the brain icon's doc count + "indexing" state
  // stay live even when the popover is closed, and the poll self-stops once every
  // document is terminal (SHU-817 R7/F1). refetchInterval returns false to stop.
  const docsQuery = useInfiniteQuery(
    personalDocsKey(kbId),
    ({ pageParam = 0 }) =>
      knowledgeBaseAPI.getDocuments(kbId, { limit: DOC_PAGE_LIMIT, offset: pageParam }).then(extractDataFromResponse),
    {
      enabled: !!kbId,
      staleTime: STALE_MS,
      getNextPageParam: (lastPage, allPages) => {
        const loaded = allPages.reduce((n, p) => n + (Array.isArray(p?.items) ? p.items.length : 0), 0);
        const lastCount = Array.isArray(lastPage?.items) ? lastPage.items.length : 0;
        const total = typeof lastPage?.total === 'number' ? lastPage.total : null;
        if (lastCount < DOC_PAGE_LIMIT) {
          return undefined;
        }
        if (total !== null && loaded >= total) {
          return undefined;
        }
        return loaded; // next offset
      },
      refetchInterval: (data) => (flattenPages(data).some(isDocNonTerminal) ? POLL_INTERVAL_MS : false),
    }
  );

  const docs = useMemo(() => flattenPages(docsQuery.data), [docsQuery.data]);
  const indexing = useMemo(() => docs.some(isDocNonTerminal), [docs]);

  const invalidatePersonalKB = useCallback(() => {
    queryClient.invalidateQueries(PERSONAL_KB_KEY);
    if (kbId) {
      queryClient.invalidateQueries(personalDocsKey(kbId));
    }
    queryClient.invalidateQueries(PICKER_KEY);
  }, [queryClient, kbId]);

  const ensurePersonalKB = useCallback(async () => {
    if (personalKB) {
      return personalKB;
    }
    if (ensurePromiseRef.current) {
      return ensurePromiseRef.current;
    }
    // Server-side idempotent ensure; the display name is derived from the user's
    // identity by the backend. Seed the cache so the docs query enables immediately.
    const promise = (async () => {
      const response = await knowledgeBaseAPI.ensurePersonal();
      const kb = extractDataFromResponse(response);
      queryClient.setQueryData(PERSONAL_KB_KEY, kb);
      return kb;
    })().finally(() => {
      ensurePromiseRef.current = null;
    });
    ensurePromiseRef.current = promise;
    return promise;
  }, [personalKB, queryClient]);

  /**
   * Upload File objects to the user's Personal Knowledge KB, auto-provisioning it
   * on first call. Returns the per-file results array. Failures are tracked by a
   * stable clientKey so retry/dismiss act on the right row even for same-named files.
   */
  const uploadFiles = useCallback(
    async (files) => {
      if (!files || files.length === 0) {
        return [];
      }
      // Tag each File with a stable clientKey up front; match results back by
      // submission index (results preserve order) rather than filename (R1).
      const tagged = Array.from(files).map((file) => ({ file, clientKey: nextClientKey(file) }));
      const batchKeys = new Set(tagged.map((t) => t.clientKey));
      inFlightUploadsRef.current += 1;
      setUploading(true);
      try {
        const kb = await ensurePersonalKB();
        const response = await knowledgeBaseAPI.uploadDocuments(
          kb.id,
          tagged.map((t) => t.file)
        );
        const data = extractDataFromResponse(response) || {};

        // Backend returns { results: [...] }; accept a bare array too and warn on
        // anything else so a future API change surfaces in logs.
        let results;
        if (Array.isArray(data)) {
          results = data;
        } else if (Array.isArray(data?.results)) {
          results = data.results;
        } else {
          log.warn('usePersonalKB: unexpected upload response shape', data);
          results = [];
        }

        const newErrors = [];
        tagged.forEach((t, i) => {
          const result = results[i];
          if (result && result.success === false) {
            newErrors.push({
              clientKey: t.clientKey,
              filename: t.file.name,
              message: result.error || 'Upload failed',
              file: t.file,
            });
          }
        });
        setErrors((prev) => [...prev.filter((e) => !batchKeys.has(e.clientKey)), ...newErrors]);

        // Refresh KB doc count, the doc list, and the picker.
        invalidatePersonalKB();
        return results;
      } catch (err) {
        log.error('usePersonalKB: upload failed', err);
        const message = err?.response?.data?.error?.message || 'Upload failed';
        const newErrors = tagged.map((t) => ({
          clientKey: t.clientKey,
          filename: t.file.name,
          message,
          file: t.file,
        }));
        setErrors((prev) => [...prev.filter((e) => !batchKeys.has(e.clientKey)), ...newErrors]);
        return [];
      } finally {
        inFlightUploadsRef.current = Math.max(0, inFlightUploadsRef.current - 1);
        if (inFlightUploadsRef.current === 0) {
          setUploading(false);
        }
      }
    },
    [ensurePersonalKB, invalidatePersonalKB]
  );

  const retryFile = useCallback(
    async (clientKey) => {
      const error = errors.find((e) => e.clientKey === clientKey);
      if (!error || !error.file) {
        return [];
      }
      // Drop the stale error row; the retried upload adds a fresh one if it fails again.
      setErrors((prev) => prev.filter((e) => e.clientKey !== clientKey));
      return uploadFiles([error.file]);
    },
    [errors, uploadFiles]
  );

  const dismissError = useCallback((clientKey) => {
    setErrors((prev) => prev.filter((e) => e.clientKey !== clientKey));
  }, []);

  // Optimistically remove a document, then reconcile (SHU-817 S1). Note: upload
  // errors (keyed by clientKey) and document rows are separate stores, so a
  // delete never needs to touch the errors list.
  const deleteDoc = useCallback(
    async (docId) => {
      if (!kbId || !docId) {
        return;
      }
      const key = personalDocsKey(kbId);
      await queryClient.cancelQueries(key);
      const previous = queryClient.getQueryData(key);
      queryClient.setQueryData(key, (old) => {
        if (!old?.pages) {
          return old;
        }
        return {
          ...old,
          pages: old.pages.map((page) => ({
            ...page,
            items: (page.items || []).filter((d) => d.id !== docId),
          })),
        };
      });
      try {
        await knowledgeBaseAPI.deleteDocument(kbId, docId);
        // Refresh the badge count + picker; the doc list is reconciled in finally.
        queryClient.invalidateQueries(PERSONAL_KB_KEY);
        queryClient.invalidateQueries(PICKER_KEY);
      } catch (err) {
        log.error('usePersonalKB: delete failed', err);
        if (previous) {
          queryClient.setQueryData(key, previous); // rollback
        }
        throw err;
      } finally {
        queryClient.invalidateQueries(key);
      }
    },
    [kbId, queryClient]
  );

  // Re-run the embed/profile pipeline for a failed/stale document from its stored
  // content (SHU-817 R3). Returns { ok, message } so the caller can surface the
  // 409 (busy) / 422 (re-upload required) message inline.
  const reingestDoc = useCallback(
    async (docId) => {
      if (!kbId || !docId) {
        return { ok: false, message: 'No document' };
      }
      try {
        await knowledgeBaseAPI.reingestDocument(kbId, docId);
        queryClient.invalidateQueries(personalDocsKey(kbId));
        return { ok: true };
      } catch (err) {
        log.error('usePersonalKB: reingest failed', err);
        const message = err?.response?.data?.error?.message || 'Could not re-ingest this document.';
        return { ok: false, message };
      }
    },
    [kbId, queryClient]
  );

  const refetchDocs = useCallback(() => {
    if (kbId) {
      queryClient.invalidateQueries(personalDocsKey(kbId));
    }
  }, [queryClient, kbId]);

  return {
    personalKB,
    loading: personalKbQuery.isLoading,
    uploading,
    errors,
    uploadFiles,
    retryFile,
    dismissError,
    refetch: invalidatePersonalKB,
    // Documents (lifted from BrainPopover — F4 / R7 / F1)
    docs,
    docsLoading: docsQuery.isLoading,
    docsFetching: docsQuery.isFetching,
    indexing,
    hasMoreDocs: !!docsQuery.hasNextPage,
    fetchMoreDocs: docsQuery.fetchNextPage,
    fetchingMoreDocs: docsQuery.isFetchingNextPage,
    refetchDocs,
    deleteDoc,
    reingestDoc,
  };
};

export default usePersonalKB;
