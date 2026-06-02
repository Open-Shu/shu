import React, { useCallback, useEffect, useRef, useState } from 'react';
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
  Zoom,
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  Check as CheckIcon,
  CheckCircleOutline as CheckCircleIcon,
  ChevronRight as ChevronRightIcon,
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
import { docStage } from './utils/docStage';

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
          role={onOpenPreview ? 'button' : undefined}
          tabIndex={onOpenPreview ? 0 : undefined}
          onKeyDown={
            onOpenPreview
              ? (e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onOpenPreview(doc);
                  }
                }
              : undefined
          }
          aria-label={onOpenPreview ? `View details for ${doc.title || 'document'}` : undefined}
          sx={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 1,
            flex: 1,
            minWidth: 0,
            px: 0.5,
            mx: -0.5,
            borderRadius: 1,
            cursor: onOpenPreview ? 'pointer' : 'default',
            transition: 'background-color 0.15s ease',
            '&:hover': onOpenPreview ? { bgcolor: 'action.hover' } : undefined,
            '&:hover .doc-row-chevron, &:focus-visible .doc-row-chevron': {
              color: 'secondary.main',
              transform: 'translateX(2px)',
            },
            '&:focus-visible': { outline: 'none', bgcolor: 'action.hover' },
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
          {onOpenPreview && (
            <ChevronRightIcon
              className="doc-row-chevron"
              aria-hidden
              sx={{
                fontSize: 18,
                color: 'text.disabled',
                alignSelf: 'center',
                flexShrink: 0,
                transition: 'color 0.15s ease, transform 0.15s ease',
              }}
            />
          )}
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
    {
      enabled: Boolean(kbId && doc?.id),
      staleTime: 30000,
      // While the document is still processing, poll so the panel fills in live
      // (stage + extracted text + stats). Stops the moment it reaches a terminal
      // state. For the non-profiling pipeline that's Ingesting → Ready.
      refetchInterval: (latest) => {
        const status = latest?.processing_info?.status || doc.processing_status;
        return docStage({ processing_status: status }).kind === 'progress' ? 2500 : false;
      },
    }
  );

  // Prefer the freshest values from the polled preview; fall back to the list row.
  const info = data?.processing_info;
  const stage = docStage({
    processing_status: info?.status || doc.processing_status,
    profiling_coverage_percent: doc.profiling_coverage_percent,
  });
  const documentType = doc.document_type || data?.document_type;
  const synopsis = data?.synopsis;
  const previewText = data?.preview;
  const wordCount = info?.word_count ?? doc.word_count;
  const chunkCount = info?.chunk_count ?? doc.chunk_count;
  const coveragePct = stage.step === 1 && typeof stage.coverage === 'number' ? ` ${Math.round(stage.coverage)}%` : '';

  // Fixed back-header + a single scrolling body. Fills 100% of the preview panel,
  // which is sized to match the list (SHU-817 item 1), so a long synopsis/extracted
  // text scrolls internally instead of growing the popover and running off the
  // bottom. The back control stays pinned.
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexShrink: 0 }}>
        <IconButton size="small" onClick={onBack} aria-label="Back to documents">
          <ArrowBackIcon fontSize="small" />
        </IconButton>
        <Typography variant="subtitle2" sx={{ flex: 1, minWidth: 0 }} noWrap title={doc.title || 'Untitled'}>
          {doc.title || 'Untitled'}
        </Typography>
      </Box>
      <Stack spacing={1.5} sx={{ flex: 1, minHeight: 0, overflowY: 'auto', mt: 1, pr: 0.5 }}>
        {/* Live processing stage. Shown only while the doc is still working; once
            Ready the chips + extracted text convey completion. Mirrors the list row. */}
        {stage.kind === 'progress' && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <StageBar step={stage.step} />
            <Typography variant="caption" color="text.secondary">
              {stage.step === 0 ? 'Ingesting…' : `Profiling…${coveragePct}`}
            </Typography>
          </Box>
        )}
        {stage.kind === 'failed' && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <ErrorOutlineIcon sx={{ fontSize: 14, color: 'error.main' }} />
            <Typography variant="caption" color="error.main">
              Processing failed
            </Typography>
          </Box>
        )}
        <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', gap: 0.5 }}>
          {documentType && <Chip size="small" label={documentType} />}
          {typeof wordCount === 'number' && wordCount > 0 && (
            <Chip size="small" variant="outlined" label={`${wordCount.toLocaleString()} words`} />
          )}
          {typeof chunkCount === 'number' && chunkCount > 0 && (
            <Chip size="small" variant="outlined" label={`${chunkCount} chunk${chunkCount === 1 ? '' : 's'}`} />
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
            {/* Profiling summary ("What's in here") renders only when a synopsis
                exists. Profiling is off in the current shipping config, so nothing
                shows here; the section returns automatically once profiling ships
                and populates the synopsis — no further change needed. */}
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
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mt: 0.5 }}>
                {previewText || (stage.kind === 'progress' ? 'Still extracting…' : 'No extracted text.')}
              </Typography>
            </Box>
          </>
        )}
      </Stack>
    </Box>
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
  // The preview panel keeps rendering its doc while it slides back out, so the
  // detail view doesn't blank mid-transition. Set synchronously on open (same
  // batch as previewDoc) so the panel never paints empty as it slides in, and
  // retained on back so it stays filled while sliding out — cleared only on close.
  const [renderedPreviewDoc, setRenderedPreviewDoc] = useState(null);
  const fileInputRef = useRef(null);

  const openPreview = useCallback((doc) => {
    setRenderedPreviewDoc(doc);
    setPreviewDoc(doc);
  }, []);

  // MUI Popover fixes the Paper's position once (at open, against the list
  // height) and never again except on window resize. Switching to the taller
  // preview — or the async /preview fetch growing the panel after that single
  // measurement — would otherwise leave the Paper anchored too low and spill off
  // the bottom (only on the first open; later opens hit the query cache). Observe
  // the content and re-run the Popover's own positioning on any size change.
  const popoverActionRef = useRef(null);
  const contentRef = useRef(null);
  useEffect(() => {
    if (!open || typeof ResizeObserver === 'undefined') {
      return undefined;
    }
    const node = contentRef.current;
    if (!node) {
      return undefined;
    }
    const observer = new ResizeObserver(() => popoverActionRef.current?.updatePosition());
    observer.observe(node);
    return () => observer.disconnect();
  }, [open]);

  // Reset the preview sub-view when the popover closes so it reopens on the list.
  const handleClose = useCallback(() => {
    setPreviewDoc(null);
    setRenderedPreviewDoc(null);
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

  // SHU-817 (item 4): for returning users the full dropzone + a separate "Add
  // files" button wasted ~110px the doc list could use. Fold both into one
  // compact control that is simultaneously the click target and the drop target,
  // surfacing the drag-over state inline.
  const compactDropTarget = (
    <Box
      role="button"
      tabIndex={0}
      aria-label="Add files to Personal Knowledge"
      onClick={handleChooseFiles}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          handleChooseFiles();
        }
      }}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 1,
        border: 1,
        borderStyle: 'dashed',
        borderColor: dragOver ? 'secondary.main' : 'divider',
        bgcolor: dragOver ? 'action.hover' : 'transparent',
        color: dragOver ? 'secondary.main' : 'text.secondary',
        borderRadius: 2,
        py: 1,
        cursor: uploading ? 'default' : 'pointer',
        transition: 'border-color 0.2s, background-color 0.2s, color 0.2s',
        '&:hover': uploading ? undefined : { borderColor: 'secondary.main', bgcolor: 'action.hover' },
        '&:focus-visible': { outline: 'none', borderColor: 'secondary.main', bgcolor: 'action.hover' },
      }}
    >
      {uploading ? <CircularProgress size={16} color="inherit" /> : <UploadFileIcon fontSize="small" />}
      <Typography variant="body2" fontWeight={500}>
        {uploading ? 'Uploading…' : dragOver ? 'Drop to add' : 'Add files or drop here'}
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
                <DocRow doc={doc} onDeleteDoc={onDeleteDoc} onReingestDoc={onReingestDoc} onOpenPreview={openPreview} />
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
      {compactDropTarget}
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

  // List and preview share one panel and push horizontally past each other
  // (SHU-817 item 1 — "swipe over on the same panel"). The ACTIVE panel stays in
  // normal flow so the container always has its real height immediately (no
  // measurement, no race with the Popover's positioning pass); the off-screen one
  // is absolutely overlaid and slid out. `visibility` flips only after the 0.32s
  // slide so the hidden panel still animates but drops out of the tab order / AT
  // tree. The outer box clips the off-screen panel.
  const panelTransition = (active) =>
    active
      ? 'transform 0.32s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease, visibility 0s'
      : 'transform 0.32s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease, visibility 0s 0.32s';
  const listActive = !previewDoc;
  const content = (
    <Box ref={contentRef} sx={{ width: { xs: '100%', sm: 360 }, overflow: 'hidden' }}>
      <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={handleFileSelect} />
      <Box sx={{ position: 'relative' }}>
        {/* The list panel ALWAYS stays in normal flow — it alone defines the
            popover's height (capped at min(70vh, 560px), scrolls internally). The
            preview panel is absolutely overlaid and fills that exact box (inset
            0), so switching views never changes the Paper size. That keeps MUI's
            one-time open-measurement valid — no reposition, no first-click spill,
            and the detail view is the same size as the list (it scrolls too). */}
        <Box
          aria-hidden={!listActive}
          sx={{
            position: 'relative',
            width: '100%',
            p: 2,
            maxHeight: 'min(70vh, 560px)',
            overflowY: 'auto',
            transform: listActive ? 'translateX(0)' : 'translateX(-100%)',
            opacity: listActive ? 1 : 0,
            visibility: listActive ? 'visible' : 'hidden',
            pointerEvents: listActive ? 'auto' : 'none',
            transition: panelTransition(listActive),
          }}
        >
          {isLoadingKB ? loadingSkeleton : isEmpty ? firstSession : returning}
          {errorList}
        </Box>
        <Box
          aria-hidden={listActive}
          sx={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            p: 2,
            transform: listActive ? 'translateX(100%)' : 'translateX(0)',
            opacity: listActive ? 0 : 1,
            visibility: listActive ? 'hidden' : 'visible',
            pointerEvents: listActive ? 'none' : 'auto',
            transition: panelTransition(!listActive),
          }}
        >
          {renderedPreviewDoc && (
            <DocPreview kbId={kb?.id} doc={renderedPreviewDoc} onBack={() => setPreviewDoc(null)} />
          )}
        </Box>
      </Box>
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

  // Emanate from the brain (SHU-817 item 3): Popover pins the Paper's
  // transform-origin to the icon corner (anchor top-left / transform bottom-left).
  // Zoom scales the panel from a point at that corner up to full size, so it
  // visibly springs out of the brain — Grow's 0.75 start scale was too small to
  // read. A back-out overshoot on the (longer) enter gives the spring; a quick
  // ease-in collapse pulls it back into the brain on close.
  return (
    <Popover
      open={open}
      action={popoverActionRef}
      anchorEl={anchorEl}
      onClose={handleClose}
      anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
      transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      TransitionComponent={Zoom}
      transitionDuration={{ appear: 300, enter: 300, exit: 190 }}
      TransitionProps={{
        easing: {
          enter: 'cubic-bezier(0.34, 1.4, 0.6, 1)',
          exit: 'cubic-bezier(0.4, 0, 1, 1)',
        },
      }}
    >
      {content}
    </Popover>
  );
});

export default BrainPopover;
