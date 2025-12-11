import React from 'react';
import {
  Box,
  Button,
  Chip,
  IconButton,
  Menu,
  MenuItem,
  TextField,
  Tooltip,
  ListItemIcon,
} from '@mui/material';
import {
  Add as AddIcon,
  AttachFile as AttachmentIcon,
  SmartToy as BotIcon,
  Send as SendIcon,
  Hub as EnsembleIcon,
} from '@mui/icons-material';

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
  onSelectEnsembleMode,
  isEnsembleModeActive,
  ensembleModeLabel,
  onClearEnsembleMode,
  ensembleMenuDisabled,
}) {
  return (
    <>
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

      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
        <input
          type="file"
          ref={fileInputRef}
          style={{ display: 'none' }}
          onChange={onFileSelected}
        />
        <Tooltip title="Add attachments or run plugins">
          <IconButton
            onClick={(e) => onPlusOpen(e.currentTarget)}
            size="medium"
            sx={{
              border: 1,
              borderColor: 'divider',
              bgcolor: 'background.paper',
              width: 36,
              height: 36,
              borderRadius: '50%',
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
        </Menu>
        <TextField
          fullWidth
          multiline
          maxRows={4}
          placeholder="Type your message..."
          value={inputMessage}
          onChange={onInputChange}
          onKeyDown={onKeyDown}
          inputRef={inputRef}
        />
        <Button
          variant="contained"
          endIcon={<SendIcon />}
          onClick={onSend}
          disabled={sendDisabled || !inputMessage.trim()}
          sx={{ minWidth: 100 }}
        >
          Send
        </Button>
      </Box>
    </>
  );
});

export default InputBar;
