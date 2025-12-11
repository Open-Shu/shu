import React from 'react';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
} from '@mui/material';

const DeleteConversationDialog = React.memo(function DeleteConversationDialog({
  open,
  conversationTitle,
  onCancel,
  onConfirm,
  isDeleting,
}) {
  return (
    <Dialog open={open} onClose={onCancel}>
      <DialogTitle>Delete Conversation</DialogTitle>
      <DialogContent>
        <DialogContentText>
          Are you sure you want to delete &quot;{conversationTitle}&quot;?
          This action cannot be undone and all messages will be permanently deleted.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel}>
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          color="error"
          variant="contained"
          disabled={isDeleting}
        >
          {isDeleting ? 'Deleting...' : 'Delete'}
        </Button>
      </DialogActions>
    </Dialog>
  );
});

export default DeleteConversationDialog;
