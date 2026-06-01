import React, { useCallback, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  FormControlLabel,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Popover,
  Stack,
  Switch,
  SwipeableDrawer,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  Check as CheckIcon,
  CheckCircleOutline as CheckCircleIcon,
  Close as CloseIcon,
  DeleteOutline as DeleteIcon,
  Description as DescriptionIcon,
  ErrorOutline as ErrorOutlineIcon,
  Refresh as RefreshIcon,
  Replay as ReplayIcon,
  Sync as SyncIcon,
  UploadFile as UploadFileIcon,
} from '@mui/icons-material';
import { useQuery } from 'react-query';
import { TransitionGroup } from 'react-transition-group';
import { extractDataFromResponse, knowledgeBaseAPI } from '../../../services/api';

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

// Collapse the 9-value pipeline status into the 3 stages users care about
// (SHU-817 S3): Ingesting → Profiling → Ready, plus a terminal Failed. Any
// terminal-success value is sticky "Ready" so a doc never looks stuck.
const docStage = (doc) => {
  const status = doc?.processing_status || 'pending';
  if (status === 'error') {
    return { kind: 'failed' };
  }
  if (TERMINAL_SUCCESS_STATUSES.has(status)) {
    return { kind: 'ready' };
  }
  if (status === 'profiling' || status === 'artifact_embedding') {
    return { kind: 'progress', step: 1, coverage: doc?.profiling_coverage_percent };
  }
  return { kind: 'progress', step: 0 };
};

// Compact 3-segment progress bar (P2) in the secondary accent, filled up to the
// current stage.
const StageBar = ({ step }) => (
  <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.4 }} aria-hidden>
    {[0, 1, 2].map((i) => (
      <Box
        key={i}
        sx={{
          height: 3,
          width: 12,
          borderRadius: 1,
          bgcolor: i <= step ? 'secondary.main' : 'divider',
          transition: 'background-color 0.3s ease',
        }}
      />
    ))}
  </Box>
);

/**
 * One document row: title + stage indicator, a delete control with inline
 * confirm (S1), and a re-ingest action for failed documents (R3). Per-row
 * confirm/busy state is local so it doesn't churn the whole list.
 */
const DocRow = ({ doc, onDeleteDoc, onReingestDoc, onOpenPreview }) => {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reingestMsg, setReingestMsg] = useState(null);

  const stage = docStage(doc);
  const canDelete = doc.source_type === 'plugin:manual_upload' && Boolean(onDeleteDoc);

  const handleDelete = useCallback(async () => {
    setBusy(true);
    try {
      await onDeleteDoc(doc.id);
    } catch {
      // The hook logs and rolls back the optimistic removal; keep the row.
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }, [doc.id, onDeleteDoc]);

  const handleRetry = useCallback(async () => {
    if (!onReingestDoc) {
      return;
    }
    setBusy(true);
    setReingestMsg(null);
    const res = await onReingestDoc(doc.id);
    if (res && !res.ok) {
      setReingestMsg(res.message);
    }
    setBusy(false);
  }, [doc.id, onReingestDoc]);

  let secondary;
  if (stage.kind === 'ready') {
    secondary = (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <CheckCircleIcon sx={{ fontSize: 14, color: 'success.main' }} />
        <Typography variant="caption" sx={{ color: 'success.main' }}>
          Ready
        </Typography>
      </Box>
    );
  } else if (stage.kind === 'failed') {
    secondary = (
      <Box>
        <Typography variant="caption" sx={{ color: 'error.main' }}>
          Failed
        </Typography>
        {reingestMsg && (
          <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
            {reingestMsg}
          </Typography>
        )}
      </Box>
    );
  } else {
    const coveragePct = stage.step === 1 && typeof stage.coverage === 'number' ? ` ${Math.round(stage.coverage)}%` : '';
    secondary = (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <StageBar step={stage.step} />
        <Typography variant="caption" color="text.secondary">
          {stage.step === 0 ? 'Ingesting…' : `Profiling…${coveragePct}`}
        </Typography>
      </Box>
    );
  }

  const actions = (
    <Stack direction="row" spacing={0.25} alignItems="center">
      {stage.kind === 'failed' && onReingestDoc && (
        <Tooltip title="Re-ingest">
          <span>
            <IconButton
              size="small"
              onClick={handleRetry}
              disabled={busy}
              aria-label={`Re-ingest ${doc.title || 'document'}`}
            >
              {busy ? <CircularProgress size={14} /> : <ReplayIcon fontSize="small" />}
            </IconButton>
          </span>
        </Tooltip>
      )}
      {canDelete &&
        (confirming ? (
          <>
            <Tooltip title="Confirm delete">
              <span>
                <IconButton
                  size="small"
                  color="error"
                  onClick={handleDelete}
                  disabled={busy}
                  aria-label="Confirm delete"
                >
                  {busy ? <CircularProgress size={14} /> : <CheckIcon fontSize="small" />}
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Cancel">
              <span>
                <IconButton
                  size="small"
                  onClick={() => setConfirming(false)}
                  disabled={busy}
                  aria-label="Cancel delete"
                >
                  <CloseIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </>
        ) : (
          <Tooltip title="Delete">
            <span>
              <IconButton
                size="small"
                onClick={() => setConfirming(true)}
                aria-label={`Delete ${doc.title || 'document'}`}
              >
                <DeleteIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        ))}
    </Stack>
  );

  return (
    <ListItem disablePadding sx={{ py: 0.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'flex-start', width: '100%', gap: 1 }}>
        <Box
          onClick={onOpenPreview ? () => onOpenPreview(doc) : undefined}
          sx={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 1,
            flex: 1,
            minWidth: 0,
            cursor: onOpenPreview ? 'pointer' : 'default',
          }}
        >
          <DescriptionIcon sx={{ fontSize: 18, color: 'text.secondary', mt: 0.25, flexShrink: 0 }} />
          <ListItemText
            sx={{ flex: 1, minWidth: 0, my: 0 }}
            primary={doc.title || 'Untitled'}
            primaryTypographyProps={{ variant: 'body2', noWrap: true }}
            secondary={secondary}
            secondaryTypographyProps={{ component: 'div' }}
          />
        </Box>
        <Box sx={{ flexShrink: 0 }}>{actions}</Box>
      </Box>
    </ListItem>
  );
};

/**
 * In-popover document preview (SHU-817 F2). A back-button sub-view (no modal,
 * per the north-star) showing the profiling synopsis ("what's in here"), the
 * document type, key stats, and an extracted-text snippet. One GET /preview call
 * supplies the synopsis + extracted text; the rest comes from the list item.
 */
const DocPreview = ({ kbId, doc, onBack }) => {
  const { data, isLoading, isError } = useQuery(
    ['personalKBDocPreview', kbId, doc.id],
    () => knowledgeBaseAPI.getDocumentPreview(kbId, doc.id, 2000).then(extractDataFromResponse),
    { enabled: Boolean(kbId && doc?.id), staleTime: 30000 }
  );

  const documentType = doc.document_type || data?.document_type;
  const synopsis = data?.synopsis;
  const previewText = data?.preview;

  return (
    <Stack spacing={1.5}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <IconButton size="small" onClick={onBack} aria-label="Back to documents">
          <ArrowBackIcon fontSize="small" />
        </IconButton>
        <Typography variant="subtitle2" sx={{ flex: 1, minWidth: 0 }} noWrap title={doc.title || 'Untitled'}>
          {doc.title || 'Untitled'}
        </Typography>
      </Box>
      <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', gap: 0.5 }}>
        {documentType && <Chip size="small" label={documentType} />}
        {typeof doc.word_count === 'number' && doc.word_count > 0 && (
          <Chip size="small" variant="outlined" label={`${doc.word_count.toLocaleString()} words`} />
        )}
        {typeof doc.chunk_count === 'number' && doc.chunk_count > 0 && (
          <Chip size="small" variant="outlined" label={`${doc.chunk_count} chunk${doc.chunk_count === 1 ? '' : 's'}`} />
        )}
        {typeof doc.profiling_coverage_percent === 'number' && (
          <Chip size="small" variant="outlined" label={`${Math.round(doc.profiling_coverage_percent)}% profiled`} />
        )}
      </Stack>
      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
          <CircularProgress size={22} />
        </Box>
      ) : isError ? (
        <Typography variant="body2" color="text.secondary">
          Couldn&apos;t load this document&apos;s preview.
        </Typography>
      ) : (
        <>
          {synopsis && (
            <Box>
              <Typography variant="caption" fontWeight={600} color="text.secondary">
                What&apos;s in here
              </Typography>
              <Typography variant="body2">{synopsis}</Typography>
            </Box>
          )}
          <Box>
            <Typography variant="caption" fontWeight={600} color="text.secondary">
              Extracted text
            </Typography>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', maxHeight: 180, overflowY: 'auto', mt: 0.5 }}>
              {previewText || 'No extracted text yet.'}
            </Typography>
          </Box>
        </>
      )}
    </Stack>
  );
};

/**
 * BrainPopover — desktop popover / mobile bottom sheet for Personal Knowledge.
 *
 * Strictly Personal Knowledge — no destination override. The document list, its
 * loading/pagination state, and the upload errors are owned by usePersonalKB
 * (React Query) and passed in as props (SHU-817 F4); this component renders them
 * and surfaces per-document delete (S1), 3-stage feedback (S3), and re-ingest (R3).
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
  onDeleteDoc,
  onReingestDoc,
  autoAttach = true,
  onToggleAutoAttach,
}) {
  const [dragOver, setDragOver] = useState(false);
  const [previewDoc, setPreviewDoc] = useState(null);
  const fileInputRef = useRef(null);

  // Reset the preview sub-view when the popover closes so it reopens on the list.
  const handleClose = useCallback(() => {
    setPreviewDoc(null);
    onClose?.();
  }, [onClose]);

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

  // SHU-817 S4 — durable per-user toggle (server-persisted). When off, the
  // personal KB isn't auto-attached to new chats; the user can still attach it
  // manually via the KB picker.
  const autoAttachToggle = onToggleAutoAttach ? (
    <FormControlLabel
      sx={{ ml: 0, mt: 0.5 }}
      control={
        <Switch
          size="small"
          checked={autoAttach}
          onChange={(event) => onToggleAutoAttach(event.target.checked)}
          inputProps={{ 'aria-label': 'Auto-attach Personal Knowledge to new chats' }}
        />
      }
      label={
        <Typography variant="caption" color="text.secondary">
          Auto-attach to new chats
        </Typography>
      }
    />
  ) : null;

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
      {autoAttachToggle}
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
          <TransitionGroup>
            {docs.map((doc) => (
              <Collapse key={doc.id}>
                <DocRow
                  doc={doc}
                  onDeleteDoc={onDeleteDoc}
                  onReingestDoc={onReingestDoc}
                  onOpenPreview={setPreviewDoc}
                />
              </Collapse>
            ))}
          </TransitionGroup>
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
      {autoAttachToggle}
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
      {previewDoc ? (
        <DocPreview kbId={kb?.id} doc={previewDoc} onBack={() => setPreviewDoc(null)} />
      ) : (
        <>
          {isLoadingKB ? loadingSkeleton : isEmpty ? firstSession : returning}
          {errorList}
        </>
      )}
    </Box>
  );

  if (isMobile) {
    return (
      <SwipeableDrawer
        anchor="bottom"
        open={open}
        onClose={handleClose}
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
      onClose={handleClose}
      anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
      transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
    >
      {content}
    </Popover>
  );
});

export default BrainPopover;
