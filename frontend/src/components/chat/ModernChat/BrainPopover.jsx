import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Popover,
  Stack,
  SwipeableDrawer,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Close as CloseIcon,
  Description as DescriptionIcon,
  ErrorOutline as ErrorOutlineIcon,
  Refresh as RefreshIcon,
  Sync as SyncIcon,
  UploadFile as UploadFileIcon,
} from '@mui/icons-material';
import { extractItemsFromResponse, knowledgeBaseAPI } from '../../../services/api';
import { log } from '../../../utils/log';

// Soft cap on docs rendered in the popover. Backend's GET /documents default
// is also 50, so we don't accidentally request more than the endpoint sends.
// Docs are sorted newest-first by Document.created_at.desc(), so currently-
// indexing uploads naturally appear at the top of the list.
const DOC_LIST_LIMIT = 50;
const POLL_INTERVAL_MS = 4000;

const dropzoneSx = (active) => ({
  border: 2,
  borderStyle: 'dashed',
  borderColor: active ? 'primary.main' : 'divider',
  borderRadius: 2,
  bgcolor: active ? 'action.hover' : 'transparent',
  p: 3,
  textAlign: 'center',
  transition: 'border-color 0.2s, background-color 0.2s',
  cursor: 'pointer',
});

// Mirrors backend DocumentStatus enum (backend/src/shu/models/document.py).
const TERMINAL_SUCCESS_STATUSES = new Set(['content_processed', 'rag_processed', 'profile_processed']);
const TERMINAL_FAILURE_STATUSES = new Set(['error']);

const docStatus = (doc) => doc?.processing_status || 'pending';

const isDocNonTerminal = (doc) => {
  const status = docStatus(doc);
  return !TERMINAL_SUCCESS_STATUSES.has(status) && !TERMINAL_FAILURE_STATUSES.has(status);
};

const docStatusLabel = (doc) => {
  const status = docStatus(doc);
  if (TERMINAL_SUCCESS_STATUSES.has(status)) {
    return { label: 'Ready', color: 'success.main' };
  }
  if (TERMINAL_FAILURE_STATUSES.has(status)) {
    return { label: 'Failed', color: 'error.main' };
  }
  return { label: 'Indexing…', color: 'text.secondary' };
};

/**
 * BrainPopover — desktop popover / mobile bottom sheet for Personal Knowledge.
 *
 * v1 scope: strictly Personal Knowledge — no destination override, no
 * "Manage all KBs →" link (added in v2 with the drawer).
 *
 * Props of note:
 *   - loading: pass true while the parent's initial KB lookup is in flight,
 *     so the popover shows a skeleton instead of the first-session prompt.
 *     Defaults to false; callers that don't pass it will see the onboarding
 *     content flash briefly before kb resolves.
 */
const BrainPopover = React.memo(function BrainPopover({
  open,
  anchorEl,
  onClose,
  isMobile,
  kb,
  loading = false,
  uploading,
  errors,
  onUpload,
  onRetry,
  onDismissError,
}) {
  const [recentDocs, setRecentDocs] = useState([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);

  // While the initial KB lookup is in flight (kb still null), show a brief
  // loading skeleton instead of the first-session prompt — otherwise users
  // with an existing Personal Knowledge see the onboarding content flash for
  // a moment before the popover swaps to "returning" view.
  const isLoadingKB = loading && !kb;
  const isEmpty = !kb || (kb.document_count || 0) === 0;

  // Fetch recent docs. Used by initial open, the polling loop, and the
  // manual refresh button. Always toggles docsLoading so the refresh icon
  // animates regardless of which path triggered the fetch.
  const fetchDocs = useCallback(async () => {
    if (!kb?.id) {
      return;
    }
    setDocsLoading(true);
    try {
      const response = await knowledgeBaseAPI.getDocuments(kb.id, {
        limit: DOC_LIST_LIMIT,
        offset: 0,
      });
      const docs = extractItemsFromResponse(response) || [];
      setRecentDocs(docs.slice(0, DOC_LIST_LIMIT));
    } catch (err) {
      log.error('BrainPopover: failed to fetch recent docs', err);
    } finally {
      setDocsLoading(false);
    }
  }, [kb?.id]);

  // Drop stale docs only when the underlying KB goes away (logout, user
  // switch). Closing the popover keeps recentDocs in state so MUI's close
  // transition doesn't visibly shrink the popover from "list of docs" to
  // "empty dropzone" mid-animation. Fresh data on reopen comes from the
  // fetch below.
  useEffect(() => {
    if (!kb?.id) {
      setRecentDocs([]);
    }
  }, [kb?.id]);

  // Refetch when the popover opens or the KB's doc count changes (after an
  // upload). Closed popover skips the fetch — no need to spin work in the
  // background while the user can't see the list.
  useEffect(() => {
    if (!open || !kb?.id) {
      return;
    }
    fetchDocs();
  }, [open, kb?.id, kb?.document_count, fetchDocs]);

  // Poll while any doc is still in a non-terminal status (Indexing…).
  // The interval clears automatically once every doc reaches a terminal state.
  // Memoized so the polling effect only re-runs on real status transitions,
  // not on every render of the parent.
  const hasNonTerminalDoc = useMemo(() => recentDocs.some(isDocNonTerminal), [recentDocs]);
  useEffect(() => {
    if (!open || !hasNonTerminalDoc) {
      return undefined;
    }
    const id = setInterval(fetchDocs, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [open, hasNonTerminalDoc, fetchDocs]);

  const handleFileSelect = useCallback(
    (event) => {
      const files = Array.from(event.target.files || []);
      if (files.length > 0 && onUpload) {
        onUpload(files);
      }
      // Reset so picking the same file twice in a row still fires onChange.
      event.target.value = '';
    },
    [onUpload]
  );

  const handleChooseFiles = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleDragOver = useCallback((event) => {
    if (event.dataTransfer?.types?.includes('Files')) {
      event.preventDefault();
      event.stopPropagation();
      setDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((event) => {
    event.preventDefault();
    event.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (event) => {
      event.preventDefault();
      event.stopPropagation();
      setDragOver(false);
      const files = Array.from(event.dataTransfer?.files || []);
      if (files.length > 0 && onUpload) {
        onUpload(files);
      }
    },
    [onUpload]
  );

  const dropzone = (
    <Box
      sx={dropzoneSx(dragOver)}
      onClick={handleChooseFiles}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <UploadFileIcon sx={{ fontSize: 32, color: 'text.secondary', mb: 1 }} />
      <Typography variant="body2" color="text.secondary">
        {dragOver ? 'Drop to add to Personal Knowledge' : 'Drop files here or click to choose'}
      </Typography>
    </Box>
  );

  const firstSession = (
    <Stack spacing={2}>
      <Box>
        <Typography variant="subtitle1" fontWeight={600} gutterBottom>
          Your Personal Knowledge
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Anything you drop here, I&apos;ll remember across every chat. Drop a CV, project notes — anything you&apos;d
          want me to know.
        </Typography>
      </Box>
      {dropzone}
      <Button
        variant="contained"
        startIcon={uploading ? <CircularProgress size={18} color="inherit" /> : <UploadFileIcon />}
        onClick={handleChooseFiles}
        disabled={uploading}
        fullWidth
      >
        {uploading ? 'Uploading…' : 'Choose files'}
      </Button>
    </Stack>
  );

  const returning = (
    <Stack spacing={2}>
      <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 1 }}>
        <Box>
          <Typography variant="subtitle1" fontWeight={600}>
            Personal Knowledge
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {kb?.document_count || 0} doc{(kb?.document_count || 0) === 1 ? '' : 's'} the assistant remembers about you
          </Typography>
        </Box>
        <Tooltip title="Refresh">
          <span>
            <IconButton size="small" onClick={fetchDocs} disabled={docsLoading} aria-label="Refresh document statuses">
              <SyncIcon
                fontSize="small"
                sx={{
                  animation: docsLoading ? 'spin 1s linear infinite' : 'none',
                  '@keyframes spin': {
                    '0%': { transform: 'rotate(0deg)' },
                    '100%': { transform: 'rotate(360deg)' },
                  },
                }}
              />
            </IconButton>
          </span>
        </Tooltip>
      </Box>
      {docsLoading && recentDocs.length === 0 ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 1 }}>
          <CircularProgress size={20} />
        </Box>
      ) : recentDocs.length > 0 ? (
        <List dense disablePadding sx={{ maxHeight: 280, overflowY: 'auto' }}>
          {recentDocs.map((doc) => {
            const status = docStatusLabel(doc);
            return (
              <ListItem key={doc.id} disablePadding sx={{ py: 0.5 }}>
                <DescriptionIcon sx={{ fontSize: 18, color: 'text.secondary', mr: 1 }} />
                <ListItemText
                  primary={doc.title || doc.filename || 'Untitled'}
                  primaryTypographyProps={{ variant: 'body2', noWrap: true }}
                  secondary={status.label}
                  secondaryTypographyProps={{ variant: 'caption', sx: { color: status.color } }}
                />
              </ListItem>
            );
          })}
        </List>
      ) : null}
      {dropzone}
      <Button
        variant="outlined"
        startIcon={uploading ? <CircularProgress size={18} color="inherit" /> : <UploadFileIcon />}
        onClick={handleChooseFiles}
        disabled={uploading}
        fullWidth
      >
        {uploading ? 'Uploading…' : 'Add files'}
      </Button>
    </Stack>
  );

  const errorList = errors && errors.length > 0 && (
    <>
      <Divider />
      <Stack spacing={1} sx={{ maxHeight: 200, overflowY: 'auto', pr: 0.5 }}>
        <Typography variant="caption" color="error.main" fontWeight={600}>
          Failed uploads ({errors.length})
        </Typography>
        {errors.map((err) => (
          <Alert
            key={err.filename}
            severity="error"
            icon={<ErrorOutlineIcon fontSize="small" />}
            action={
              <Stack direction="row" spacing={0.5}>
                {err.file && (
                  <IconButton size="small" onClick={() => onRetry?.(err.filename)} aria-label="Retry upload">
                    <RefreshIcon fontSize="small" />
                  </IconButton>
                )}
                <IconButton size="small" onClick={() => onDismissError?.(err.filename)} aria-label="Dismiss error">
                  <CloseIcon fontSize="small" />
                </IconButton>
              </Stack>
            }
            sx={{ py: 0.25 }}
          >
            <Typography variant="caption" component="span" fontWeight={600}>
              {err.filename}
            </Typography>
            <Typography variant="caption" component="span" sx={{ ml: 0.5 }}>
              — {err.message}
            </Typography>
          </Alert>
        ))}
      </Stack>
    </>
  );

  const loadingSkeleton = (
    <Box sx={{ display: 'flex', justifyContent: 'center', py: 5 }}>
      <CircularProgress size={28} />
    </Box>
  );

  const content = (
    <Box sx={{ p: 2, width: { xs: '100%', sm: 360 } }}>
      <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={handleFileSelect} />
      {isLoadingKB ? loadingSkeleton : isEmpty ? firstSession : returning}
      {errorList}
    </Box>
  );

  if (isMobile) {
    return (
      <SwipeableDrawer
        anchor="bottom"
        open={open}
        onClose={onClose}
        onOpen={() => {}}
        disableSwipeToOpen
        PaperProps={{ sx: { borderTopLeftRadius: 16, borderTopRightRadius: 16 } }}
      >
        <Box sx={{ width: 32, height: 4, bgcolor: 'divider', borderRadius: 2, mx: 'auto', mt: 1, mb: 1 }} />
        {content}
      </SwipeableDrawer>
    );
  }

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
      transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
    >
      {content}
    </Popover>
  );
});

export default BrainPopover;
