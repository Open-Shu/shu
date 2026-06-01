import React, { useCallback, useRef, useState } from 'react';
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

const dropzoneSx = (active) => ({
  border: 2,
  borderStyle: 'dashed',
  borderColor: active ? 'secondary.main' : 'divider',
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
 * Strictly Personal Knowledge — no destination override. The document list,
 * its loading/pagination state, and the upload errors are owned by usePersonalKB
 * (React Query) and passed in as props (SHU-817 F4), so the brain badge and the
 * popover stay consistent and the poll self-stops when nothing is indexing.
 *
 * Props of note:
 *   - loading: pass true while the parent's initial KB lookup is in flight, so
 *     the popover shows a skeleton instead of the first-session prompt.
 *   - docs / docsLoading / docsFetching / hasMoreDocs / fetchMoreDocs /
 *     fetchingMoreDocs / onRefreshDocs: the personal-KB document list, supplied
 *     by usePersonalKB.
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
  docs = [],
  docsLoading = false,
  docsFetching = false,
  hasMoreDocs = false,
  fetchMoreDocs,
  fetchingMoreDocs = false,
  onRefreshDocs,
}) {
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);

  // While the initial KB lookup is in flight (kb still null), show a brief
  // loading skeleton instead of the first-session prompt — otherwise users with
  // an existing Personal Knowledge see the onboarding content flash before the
  // popover swaps to "returning" view.
  const isLoadingKB = loading && !kb;
  const isEmpty = !kb || (kb.document_count || 0) === 0;

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
          Drop documents here and ask me anything about them later. I&apos;ll find the passages that match your question
          and ground my answer in your own material.
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
            {kb?.document_count || 0} doc{(kb?.document_count || 0) === 1 ? '' : 's'} I can search to answer your
            questions
          </Typography>
        </Box>
        <Tooltip title="Refresh">
          <span>
            <IconButton
              size="small"
              onClick={onRefreshDocs}
              disabled={docsFetching}
              aria-label="Refresh document statuses"
            >
              <SyncIcon
                fontSize="small"
                sx={{
                  animation: docsFetching ? 'spin 1s linear infinite' : 'none',
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
      {docsLoading && docs.length === 0 ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 1 }}>
          <CircularProgress size={20} />
        </Box>
      ) : docs.length > 0 ? (
        <List dense disablePadding sx={{ maxHeight: 280, overflowY: 'auto' }}>
          {docs.map((doc) => {
            const status = docStatusLabel(doc);
            return (
              <ListItem key={doc.id} disablePadding sx={{ py: 0.5 }}>
                <DescriptionIcon sx={{ fontSize: 18, color: 'text.secondary', mr: 1 }} />
                <ListItemText
                  primary={doc.title || 'Untitled'}
                  primaryTypographyProps={{ variant: 'body2', noWrap: true }}
                  secondary={status.label}
                  secondaryTypographyProps={{ variant: 'caption', sx: { color: status.color } }}
                />
              </ListItem>
            );
          })}
        </List>
      ) : null}
      {hasMoreDocs && (
        <Button
          size="small"
          onClick={() => fetchMoreDocs?.()}
          disabled={fetchingMoreDocs}
          startIcon={fetchingMoreDocs ? <CircularProgress size={14} color="inherit" /> : null}
        >
          {fetchingMoreDocs ? 'Loading…' : 'Show more'}
        </Button>
      )}
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
          {errors.length} upload{errors.length === 1 ? '' : 's'} need attention
        </Typography>
        {errors.map((err) => (
          <Alert
            key={err.clientKey}
            severity="error"
            icon={<ErrorOutlineIcon fontSize="small" />}
            action={
              <Stack direction="row" spacing={0.5}>
                {err.file && (
                  <IconButton size="small" onClick={() => onRetry?.(err.clientKey)} aria-label="Retry upload">
                    <RefreshIcon fontSize="small" />
                  </IconButton>
                )}
                <IconButton size="small" onClick={() => onDismissError?.(err.clientKey)} aria-label="Dismiss error">
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
