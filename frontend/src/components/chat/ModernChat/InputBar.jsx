import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Snackbar,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Add as AddIcon,
  AttachFile as AttachmentIcon,
  Extension as PluginIcon,
  Send as SendIcon,
  Stop as StopIcon,
  Hub as EnsembleIcon,
  LibraryBooks as LibraryBooksIcon,
  Psychology as PsychologyIcon,
} from '@mui/icons-material';
import BrainIcon from './BrainIcon';
import BrainPopover from './BrainPopover';

const InputBar = React.memo(function InputBar({
  pendingAttachments,
  onRemoveAttachment,
  attachmentChipStyles,
  inputMessage,
  onInputChange,
  onKeyDown,
  onSend,
  sendDisabled,
  inputRef,
  fileInputRef,
  onFileSelected,
  plusAnchorEl,
  onPlusOpen,
  onPlusClose,
  isUploadingAttachment,
  onOpenPluginPicker,
  pluginsEnabled,
  onUploadClick,
  onOpenKBPicker,
  selectedKBs,
  onRemoveKB,
  onSelectEnsembleMode,
  isEnsembleModeActive,
  ensembleModeLabel,
  onClearEnsembleMode,
  ensembleMenuDisabled,
  // Personal Knowledge (v1) — brain icon, popover, drag/drop, paste
  personalKB,
  personalKBLoading = false,
  personalKBUploading = false,
  personalKBErrors = [],
  onUploadToPersonalKB,
  onRetryPersonalKBFile,
  onDismissPersonalKBError,
  personalKBDocs = [],
  personalKBDocsLoading = false,
  personalKBDocsFetching = false,
  personalKBDocsError = false,
  personalKBIndexing = false,
  personalKBHasMoreDocs = false,
  onFetchMorePersonalKBDocs,
  personalKBFetchingMoreDocs = false,
  onRefreshPersonalKBDocs,
  onDeletePersonalKBDoc,
  onReingestPersonalKBDoc,
  personalKBAutoAttach = true,
  onTogglePersonalKBAutoAttach,
  // SHU-803: Send button morphs into Stop while the current conversation
  // has an in-flight stream. `canStop` is false during the ~10-50ms
  // window after Send before stream_start arrives — disabled state with
  // "Initializing…" tooltip is more discoverable than no button at all.
  isStreaming = false,
  canStop = false,
  onStop,
  isMobile = false,
}) {
  const [brainAnchorEl, setBrainAnchorEl] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const [toast, setToast] = useState(null);
  const [stopping, setStopping] = useState(false);

  const handleStopClick = useCallback(async () => {
    if (!onStop || stopping) {
      return;
    }
    setStopping(true);
    try {
      await onStop();
    } finally {
      setStopping(false);
    }
  }, [onStop, stopping]);

  useEffect(() => {
    if (!isStreaming) {
      setStopping(false);
    }
  }, [isStreaming]);

  const dragCounterRef = useRef(0);
  const wasUploadingRef = useRef(false);
  const errorCountAtStartRef = useRef(0);

  const handleBrainClick = useCallback((event) => {
    setBrainAnchorEl(event.currentTarget);
  }, []);

  const handleBrainPopoverClose = useCallback(() => setBrainAnchorEl(null), []);

  const handleBrainUpload = useCallback(
    (files) => {
      onUploadToPersonalKB?.(files);
    },
    [onUploadToPersonalKB]
  );

  // Browser-native drag-from-outside detection so accidental in-page drags don't trigger overlay.
  const isFileDrag = (event) => Boolean(event.dataTransfer?.types?.includes('Files'));

  const handleDragEnter = useCallback((event) => {
    if (!isFileDrag(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragCounterRef.current += 1;
    setDragActive(true);
  }, []);

  const handleDragOver = useCallback((event) => {
    if (!isFileDrag(event)) {
      return;
    }
    event.preventDefault();
  }, []);

  const handleDragLeave = useCallback((event) => {
    event.preventDefault();
    event.stopPropagation();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback(
    (event) => {
      if (!isFileDrag(event)) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      dragCounterRef.current = 0;
      setDragActive(false);
      const files = Array.from(event.dataTransfer?.files || []);
      if (files.length > 0 && onUploadToPersonalKB) {
        onUploadToPersonalKB(files);
      }
    },
    [onUploadToPersonalKB]
  );

  // The brain popover is rendered through a Portal, so it sits outside the
  // InputBar wrapper's DOM subtree but still bubbles React events back up
  // through the React tree. When the popover handles a drop it calls
  // stopPropagation on the synthetic event, which React forwards to the
  // underlying native event — so a bubble-phase window listener never sees
  // the drop and dragCounterRef stays incremented (chat overlay + brain
  // vortex stuck on). Capture phase runs window→target before any handler
  // can call stopPropagation, so it fires reliably regardless of which
  // element captures the drop.
  useEffect(() => {
    const resetDragState = () => {
      dragCounterRef.current = 0;
      setDragActive(false);
    };
    window.addEventListener('drop', resetDragState, true);
    window.addEventListener('dragend', resetDragState, true);
    return () => {
      window.removeEventListener('drop', resetDragState, true);
      window.removeEventListener('dragend', resetDragState, true);
    };
  }, []);

  // Paste handler: route files / pasted images to Personal Knowledge.
  // Plain-text paste passes through unchanged so users' typing flow isn't disturbed.
  const handlePaste = useCallback(
    (event) => {
      const cd = event.clipboardData;
      if (!cd) {
        return;
      }

      let files = Array.from(cd.files || []);

      if (files.length === 0) {
        const imageItems = Array.from(cd.items || []).filter(
          (item) => item.kind === 'file' && item.type.startsWith('image/')
        );
        const imageFiles = imageItems.map((item) => item.getAsFile()).filter(Boolean);
        if (imageFiles.length > 0) {
          const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
          files = imageFiles.map((f, idx) => {
            const ext = f.type.split('/')[1] || 'png';
            return new File([f], `pasted-${ts}-${idx}.${ext}`, { type: f.type });
          });
        }
      }

      if (files.length > 0 && onUploadToPersonalKB) {
        event.preventDefault();
        onUploadToPersonalKB(files);
      }
      // No files in clipboard: let the browser handle plain-text paste normally.
    },
    [onUploadToPersonalKB]
  );

  // Toast on upload transitions: capture error count at upload start so we can
  // diff against the post-upload error count to know what just happened.
  useEffect(() => {
    if (personalKBUploading && !wasUploadingRef.current) {
      wasUploadingRef.current = true;
      errorCountAtStartRef.current = personalKBErrors.length;
    } else if (!personalKBUploading && wasUploadingRef.current) {
      wasUploadingRef.current = false;
      const newErrors = Math.max(0, personalKBErrors.length - errorCountAtStartRef.current);
      if (newErrors === 0) {
        setToast({ severity: 'success', message: 'Got it — ask me about this anytime' });
      } else {
        setToast({
          severity: 'error',
          message: `${newErrors} file${newErrors === 1 ? '' : 's'} need attention — check the brain icon`,
        });
      }
    }
  }, [personalKBUploading, personalKBErrors]);

  return (
    <Box
      sx={{ position: 'relative', width: '100%' }}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {dragActive && (
        <Box
          sx={{
            position: 'absolute',
            inset: 0,
            zIndex: 10,
            bgcolor: 'action.hover',
            border: 2,
            borderStyle: 'dashed',
            borderColor: 'secondary.main',
            borderRadius: 2,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
          }}
        >
          <Typography variant="h6" color="secondary.main" fontWeight={600}>
            Drop to add to Personal Knowledge
          </Typography>
        </Box>
      )}

      {pendingAttachments.length > 0 && (
        <Box sx={{ mb: 1, display: 'flex', flexWrap: 'wrap', gap: 1 }}>
          {pendingAttachments.map((attachment) => (
            <Chip
              key={attachment.id}
              label={attachment.name}
              onDelete={() => onRemoveAttachment(attachment.id)}
              sx={attachmentChipStyles}
            />
          ))}
        </Box>
      )}

      {isEnsembleModeActive && onClearEnsembleMode && (
        <Box sx={{ mb: 1, display: 'flex', flexWrap: 'wrap', gap: 1 }}>
          <Chip
            label={ensembleModeLabel || 'Ensemble mode active'}
            color="primary"
            onDelete={onClearEnsembleMode}
            variant="outlined"
          />
        </Box>
      )}

      {selectedKBs && selectedKBs.length > 0 && (
        <Box sx={{ mb: 1, display: 'flex', flexWrap: 'wrap', gap: 1 }}>
          {selectedKBs.map((kb) => {
            // Distinguish the auto-attached Personal Knowledge chip from other
            // attached KBs with the brain glyph + a filled variant (SHU-817 R4).
            const isPersonal = kb.is_personal === true || (personalKB && kb.id === personalKB.id);
            return (
              <Chip
                key={kb.id}
                label={kb.name}
                color="secondary"
                onDelete={onRemoveKB ? () => onRemoveKB(kb.id) : undefined}
                variant={isPersonal ? 'filled' : 'outlined'}
                icon={isPersonal ? <PsychologyIcon /> : <LibraryBooksIcon />}
              />
            );
          })}
        </Box>
      )}

      <Box sx={{ display: 'flex', gap: { xs: 0.5, sm: 1 }, alignItems: 'center' }}>
        <input type="file" ref={fileInputRef} style={{ display: 'none' }} onChange={onFileSelected} />
        <Tooltip title="Add attachments or run plugins">
          <IconButton
            onClick={(e) => onPlusOpen(e.currentTarget)}
            size="medium"
            sx={{
              border: 1,
              borderColor: 'divider',
              bgcolor: 'background.paper',
              width: { xs: 40, sm: 36 },
              height: { xs: 40, sm: 36 },
              borderRadius: '50%',
              flexShrink: 0,
            }}
            aria-label="Open actions menu"
          >
            <AddIcon />
          </IconButton>
        </Tooltip>
        <Menu
          anchorEl={plusAnchorEl}
          open={Boolean(plusAnchorEl)}
          onClose={onPlusClose}
          anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
          transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        >
          <MenuItem
            onClick={() => {
              onPlusClose();
              onUploadClick();
            }}
            disabled={isUploadingAttachment}
          >
            <ListItemIcon>
              <AttachmentIcon fontSize="small" />
            </ListItemIcon>
            Add documents
          </MenuItem>
          {onSelectEnsembleMode && (
            <MenuItem
              onClick={() => {
                onPlusClose();
                onSelectEnsembleMode();
              }}
              disabled={ensembleMenuDisabled}
            >
              <ListItemIcon>
                <EnsembleIcon fontSize="small" />
              </ListItemIcon>
              {isEnsembleModeActive ? 'Edit ensemble mode' : 'Configure ensemble mode'}
            </MenuItem>
          )}
          {pluginsEnabled && (
            <MenuItem
              onClick={() => {
                onPlusClose();
                onOpenPluginPicker();
              }}
            >
              <ListItemIcon>
                <PluginIcon fontSize="small" />
              </ListItemIcon>
              Use a plugin
            </MenuItem>
          )}
          {onOpenKBPicker && (
            <MenuItem
              onClick={() => {
                onPlusClose();
                onOpenKBPicker();
              }}
            >
              <ListItemIcon>
                <LibraryBooksIcon fontSize="small" />
              </ListItemIcon>
              Attach Knowledge Base
            </MenuItem>
          )}
        </Menu>
        <BrainIcon
          kb={personalKB}
          uploading={personalKBUploading}
          indexing={personalKBIndexing}
          errorCount={personalKBErrors.length}
          dragActive={dragActive}
          onClick={handleBrainClick}
        />
        <TextField
          fullWidth
          multiline
          maxRows={4}
          placeholder="Type your message..."
          value={inputMessage}
          onChange={onInputChange}
          onKeyDown={onKeyDown}
          onPaste={handlePaste}
          inputRef={inputRef}
          size={isMobile ? 'small' : 'medium'}
          sx={{
            '& .MuiInputBase-root': {
              minHeight: { xs: 40, sm: 'auto' },
            },
          }}
        />
        {/* SHU-803: while streaming, the Send button becomes a Stop
            button anchored in the same spot. This keeps the control
            within thumb reach on mobile and at a predictable location
            on desktop (Send and Stop never coexist — streaming blocks
            input). We use color="inherit" / a neutral grey background
            so Stop reads as a secondary action — we don't want users
            terminating streams as a reflex. */}
        {isStreaming ? (
          isMobile ? (
            <Tooltip title={canStop ? 'Stop generating' : 'Initializing…'}>
              <span>
                <IconButton
                  onClick={handleStopClick}
                  disabled={!canStop || stopping || !onStop}
                  sx={{
                    bgcolor: 'action.selected',
                    color: 'text.primary',
                    width: 44,
                    height: 44,
                    flexShrink: 0,
                    '&:hover': {
                      bgcolor: 'action.hover',
                    },
                  }}
                  aria-label="Stop generating"
                >
                  {stopping ? <CircularProgress size={20} color="inherit" /> : <StopIcon />}
                </IconButton>
              </span>
            </Tooltip>
          ) : (
            <Tooltip title={canStop ? 'Stop generating' : 'Initializing…'}>
              <span>
                <Button
                  variant="contained"
                  disableElevation
                  startIcon={stopping ? <CircularProgress size={16} color="inherit" /> : <StopIcon />}
                  onClick={handleStopClick}
                  disabled={!canStop || stopping || !onStop}
                  aria-label="Stop generating"
                  sx={{
                    minWidth: 100,
                    flexShrink: 0,
                    bgcolor: 'action.selected',
                    color: 'text.primary',
                    '&:hover': {
                      bgcolor: 'action.hover',
                    },
                  }}
                >
                  Stop
                </Button>
              </span>
            </Tooltip>
          )
        ) : isMobile ? (
          <Tooltip title="Send">
            <span>
              <IconButton
                color="primary"
                onClick={onSend}
                disabled={sendDisabled || !inputMessage.trim()}
                sx={{
                  bgcolor: 'primary.main',
                  color: 'primary.contrastText',
                  width: 44,
                  height: 44,
                  flexShrink: 0,
                  '&:hover': {
                    bgcolor: 'primary.dark',
                  },
                  '&.Mui-disabled': {
                    bgcolor: 'action.disabledBackground',
                    color: 'action.disabled',
                  },
                }}
                aria-label="Send message"
              >
                <SendIcon />
              </IconButton>
            </span>
          </Tooltip>
        ) : (
          <Button
            variant="contained"
            endIcon={<SendIcon />}
            onClick={onSend}
            disabled={sendDisabled || !inputMessage.trim()}
            sx={{ minWidth: 100, flexShrink: 0 }}
          >
            Send
          </Button>
        )}
      </Box>

      <BrainPopover
        open={Boolean(brainAnchorEl)}
        anchorEl={brainAnchorEl}
        onClose={handleBrainPopoverClose}
        isMobile={isMobile}
        kb={personalKB}
        loading={personalKBLoading}
        uploading={personalKBUploading}
        errors={personalKBErrors}
        onUpload={handleBrainUpload}
        onRetry={onRetryPersonalKBFile}
        onDismissError={onDismissPersonalKBError}
        docs={personalKBDocs}
        docsLoading={personalKBDocsLoading}
        docsFetching={personalKBDocsFetching}
        docsError={personalKBDocsError}
        hasMoreDocs={personalKBHasMoreDocs}
        fetchMoreDocs={onFetchMorePersonalKBDocs}
        fetchingMoreDocs={personalKBFetchingMoreDocs}
        onRefreshDocs={onRefreshPersonalKBDocs}
        onDeleteDoc={onDeletePersonalKBDoc}
        onReingestDoc={onReingestPersonalKBDoc}
        autoAttach={personalKBAutoAttach}
        onToggleAutoAttach={onTogglePersonalKBAutoAttach}
      />

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        {toast ? (
          <Alert severity={toast.severity} onClose={() => setToast(null)} variant="filled" sx={{ width: '100%' }}>
            {toast.message}
          </Alert>
        ) : null}
      </Snackbar>
    </Box>
  );
});

export default InputBar;
