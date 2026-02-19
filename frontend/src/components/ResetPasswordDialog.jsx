import React, { useState } from 'react';
import { useMutation } from 'react-query';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Alert,
  Typography,
  Box,
  TextField,
  IconButton,
} from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { authAPI, extractDataFromResponse, formatError } from '../services/api';

const COPY_FEEDBACK_MS = 2000;

const resolveUserId = (user) => {
  if (!user) {
    return '';
  }
  return user.user_id || user.id || '';
};

/** Phase 1: confirmation prompt before resetting. */
const ConfirmPhase = ({ userName }) => (
  <>
    <Typography variant="body1" gutterBottom>
      Reset password for {userName}?
    </Typography>
    <Typography variant="body2" color="text.secondary" gutterBottom>
      A temporary password will be generated. The user will be required to change it on next login.
    </Typography>
  </>
);

/** Phase 2: display the generated temporary password with copy action. */
const ResultPhase = ({ userName, temporaryPassword, copied, onCopy }) => (
  <>
    <Typography variant="body1" gutterBottom>
      Temporary password for {userName}:
    </Typography>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1, mb: 2 }}>
      <TextField fullWidth value={temporaryPassword} InputProps={{ readOnly: true }} size="small" />
      <IconButton onClick={onCopy} size="small" color={copied ? 'success' : 'default'} title="Copy to clipboard">
        <ContentCopyIcon />
      </IconButton>
    </Box>
    {copied && (
      <Typography variant="caption" color="success.main">
        Copied!
      </Typography>
    )}
    <Alert severity="warning" sx={{ mt: 2 }}>
      This password will not be shown again. Please share it securely with the user.
    </Alert>
  </>
);

const ResetPasswordDialog = ({ open, onClose, user, onSuccess }) => {
  const [temporaryPassword, setTemporaryPassword] = useState(null);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  const resetPasswordMutation = useMutation((userId) => authAPI.resetUserPassword(userId), {
    onSuccess: (response) => {
      const data = extractDataFromResponse(response);
      setTemporaryPassword(data.temporary_password);
      setError(null);
      if (onSuccess) {
        onSuccess(data.message || 'Password reset successfully.');
      }
    },
    onError: (err) => {
      const formatted = formatError(err);
      setError(typeof formatted === 'string' ? formatted : formatted?.message || 'Failed to reset password');
    },
  });

  const handleConfirmReset = () => {
    const userId = resolveUserId(user);
    if (!userId) {
      setError('Unable to reset password: missing user identifier');
      return;
    }
    resetPasswordMutation.mutate(userId);
  };

  const handleCopyPassword = async () => {
    if (temporaryPassword) {
      try {
        await navigator.clipboard.writeText(temporaryPassword);
        setCopied(true);
        setTimeout(() => setCopied(false), COPY_FEEDBACK_MS);
      } catch {
        // Fallback: user can manually copy from the text field
      }
    }
  };

  const handleClose = () => {
    setTemporaryPassword(null);
    setError(null);
    setCopied(false);
    resetPasswordMutation.reset();
    onClose();
  };

  const isPhaseTwo = !!temporaryPassword;

  return (
    <Dialog open={open} onClose={handleClose}>
      <DialogTitle>Reset Password</DialogTitle>
      <DialogContent>
        {user && (
          <Box sx={{ pt: 1 }}>
            {!isPhaseTwo ? (
              <ConfirmPhase userName={user.name} />
            ) : (
              <ResultPhase
                userName={user.name}
                temporaryPassword={temporaryPassword}
                copied={copied}
                onCopy={handleCopyPassword}
              />
            )}
            {error && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {error}
              </Alert>
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        {!isPhaseTwo ? (
          <>
            <Button onClick={handleClose}>Cancel</Button>
            <Button
              onClick={handleConfirmReset}
              variant="contained"
              color="warning"
              disabled={resetPasswordMutation.isLoading}
            >
              {resetPasswordMutation.isLoading ? 'Resetting...' : 'Reset Password'}
            </Button>
          </>
        ) : (
          <Button onClick={handleClose} variant="contained">
            Done
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default ResetPasswordDialog;
