import React from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  TextField,
} from "@mui/material";

const RenameConversationDialog = React.memo(function RenameConversationDialog({
  open,
  value,
  error,
  onChange,
  onCancel,
  onConfirm,
  isSaving,
}) {
  return (
    <Dialog open={open} onClose={onCancel}>
      <DialogTitle>Rename Conversation</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          Update the name of this conversation to help you find it later.
        </DialogContentText>
        <TextField
          autoFocus
          fullWidth
          label="Conversation Title"
          value={value}
          onChange={onChange}
          disabled={isSaving}
          error={Boolean(error)}
          helperText={error}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={isSaving}>
          Cancel
        </Button>
        <Button onClick={onConfirm} variant="contained" disabled={isSaving}>
          {isSaving ? "Saving..." : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
});

export default RenameConversationDialog;
