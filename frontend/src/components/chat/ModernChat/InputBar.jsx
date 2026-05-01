import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
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
  SmartToy as BotIcon,
  Send as SendIcon,
  Hub as EnsembleIcon,
  LibraryBooks as LibraryBooksIcon,
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
  personalKBUploading = false,
  personalKBErrors = [],
  onUploadToPersonalKB,
  onRetryPersonalKBFile,
  onDismissPersonalKBError,
  isMobile = false,
}) {
  const [brainAnchorEl, setBrainAnchorEl] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const [toast, setToast] = useState(null);

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
        setToast({ severity: 'success', message: 'Added to Personal Knowledge' });
      } else {
        setToast({
          severity: 'error',
          message: `${newErrors} upload${newErrors === 1 ? '' : 's'} failed — see the brain icon`,
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
            bgcolor: 'rgba(25, 118, 210, 0.08)',
            border: 2,
            borderStyle: 'dashed',
            borderColor: 'primary.main',
            borderRadius: 2,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
          }}
        >
          <Typography variant="h6" color="primary.main" fontWeight={600}>
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
          {selectedKBs.map((kb) => (
            <Chip
              key={kb.id}
              label={kb.name}
              color="secondary"
              onDelete={onRemoveKB ? () => onRemoveKB(kb.id) : undefined}
              variant="outlined"
              icon={<LibraryBooksIcon />}
            />
          ))}
        </Box>
      )}

      <Box sx={{ display: 'flex', gap: { xs: 0.5, sm: 1 }, alignItems: 'center' }}>
        <BrainIcon
          kb={personalKB}
          uploading={personalKBUploading}
          errorCount={personalKBErrors.length}
          dragActive={dragActive}
          onClick={handleBrainClick}
        />
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
                <BotIcon fontSize="small" />
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
        {/* On mobile: icon-only send button. On desktop: full button with text */}
        {isMobile ? (
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
        uploading={personalKBUploading}
        errors={personalKBErrors}
        onUpload={handleBrainUpload}
        onRetry={onRetryPersonalKBFile}
        onDismissError={onDismissPersonalKBError}
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
