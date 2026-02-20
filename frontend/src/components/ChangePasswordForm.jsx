import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Container,
  IconButton,
  InputAdornment,
  Paper,
  TextField,
  Typography,
} from '@mui/material';
import Visibility from '@mui/icons-material/Visibility';
import VisibilityOff from '@mui/icons-material/VisibilityOff';
import LockIcon from '@mui/icons-material/Lock';
import LogoutIcon from '@mui/icons-material/Logout';

import { useAuth } from '../hooks/useAuth';
import { authAPI, formatError } from '../services/api';
import { validatePassword, PasswordRequirements } from '../utils/passwordValidation';

/** Check whether the new password is identical to the current one. */
const isSamePassword = (current, newPw) => current.length > 0 && newPw.length > 0 && current === newPw;

/** Determine whether the form is ready for submission. */
const isFormReady = (current, newPw, confirm, validation, passwordsMatch, sameAsCurrent, loading) =>
  current.length > 0 &&
  newPw.length > 0 &&
  confirm.length > 0 &&
  validation.valid &&
  passwordsMatch &&
  !sameAsCurrent &&
  !loading;

/** Reusable password field with visibility toggle. */
const PasswordField = ({ label, value, onChange, show, onToggleShow, disabled, autoComplete, error, helperText }) => (
  <TextField
    margin="normal"
    required
    fullWidth
    label={label}
    type={show ? 'text' : 'password'}
    value={value}
    onChange={onChange}
    disabled={disabled}
    autoComplete={autoComplete}
    error={error}
    helperText={helperText}
    InputProps={{
      endAdornment: (
        <InputAdornment position="end">
          <IconButton
            aria-label={`toggle ${label.toLowerCase()} visibility`}
            onClick={onToggleShow}
            edge="end"
            size="small"
          >
            {show ? <VisibilityOff /> : <Visibility />}
          </IconButton>
        </InputAdornment>
      ),
    }}
  />
);

/** SSO info banner shown when user cannot change password. */
const SsoInfo = ({ authMethod }) => {
  const providerName = authMethod.charAt(0).toUpperCase() + authMethod.slice(1);
  return (
    <Alert severity="info" sx={{ mt: 2 }}>
      Password change is not available for {providerName} SSO accounts. Your password is managed by your identity
      provider.
    </Alert>
  );
};

/** Force-mode header, status alerts, or nothing — rendered above the form fields. */
const FormHeader = ({ forceMode, error, success }) => (
  <>
    {forceMode && (
      <Box sx={{ textAlign: 'center', mb: 3 }}>
        <LockIcon sx={{ fontSize: 48, color: 'warning.main', mb: 1 }} />
        <Typography variant="h5" gutterBottom>
          Password Change Required
        </Typography>
        <Typography variant="body1" color="text.secondary">
          Your administrator has reset your password. You must choose a new password before continuing. Please enter the
          temporary password provided by your administrator.
        </Typography>
      </Box>
    )}
    {error && (
      <Alert severity="error" sx={{ mb: 2 }}>
        {error}
      </Alert>
    )}
    {success && (
      <Alert severity="success" sx={{ mb: 2 }}>
        {success}
      </Alert>
    )}
  </>
);

/** Submit button with loading state. */
const SubmitButton = ({ loading, disabled }) => (
  <Button
    type="submit"
    fullWidth
    variant="contained"
    size="large"
    disabled={disabled}
    startIcon={loading ? <CircularProgress size={20} /> : <LockIcon />}
    sx={{ mt: 3, mb: 2, py: 1.5 }}
  >
    {loading ? 'Changing Password...' : 'Change Password'}
  </Button>
);

/** The three password input fields with inline validation. */
const PasswordFormFields = ({
  currentPassword,
  newPassword,
  confirmPassword,
  onCurrentChange,
  onNewChange,
  onConfirmChange,
  showCurrentPassword,
  showNewPassword,
  showConfirmPassword,
  onToggleCurrent,
  onToggleNew,
  onToggleConfirm,
  loading,
  sameAsCurrent,
  passwordsMatch,
}) => (
  <>
    <PasswordField
      label="Current Password"
      value={currentPassword}
      onChange={onCurrentChange}
      show={showCurrentPassword}
      onToggleShow={onToggleCurrent}
      disabled={loading}
      autoComplete="current-password"
    />

    <PasswordField
      label="New Password"
      value={newPassword}
      onChange={onNewChange}
      show={showNewPassword}
      onToggleShow={onToggleNew}
      disabled={loading}
      autoComplete="new-password"
      error={sameAsCurrent}
      helperText={sameAsCurrent ? 'New password must be different from current password' : ''}
    />

    {newPassword.length > 0 && <PasswordRequirements password={newPassword} />}

    <PasswordField
      label="Confirm New Password"
      value={confirmPassword}
      onChange={onConfirmChange}
      show={showConfirmPassword}
      onToggleShow={onToggleConfirm}
      disabled={loading}
      autoComplete="new-password"
      error={confirmPassword.length > 0 && !passwordsMatch}
      helperText={confirmPassword.length > 0 && !passwordsMatch ? 'Passwords do not match' : ''}
    />
  </>
);

/** Centered container used for forced password change screen. */
const ForceWrapper = ({ children, onLogout }) => (
  <Container maxWidth="sm">
    <Box sx={{ marginTop: 8, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <Paper elevation={3} sx={{ padding: 4, width: '100%' }}>
        {children}
      </Paper>
      {onLogout && (
        <Button onClick={onLogout} startIcon={<LogoutIcon />} sx={{ mt: 2 }} color="inherit" size="small">
          Log out
        </Button>
      )}
    </Box>
  </Container>
);

/**
 * Change password form with real-time validation.
 *
 * @param {{ onSuccess?: () => void, forceMode?: boolean, onLogout?: () => void }} props
 * - forceMode: renders as a full-page centered card with explanation text
 * - onLogout: if provided, a logout button is shown in force mode
 * - default: renders as a card for embedding in UserPreferencesPage
 */
const ChangePasswordForm = ({ onSuccess, forceMode = false, onLogout }) => {
  const { user, refreshUser } = useAuth();

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const [showCurrentPassword, setShowCurrentPassword] = useState(false);
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  if (user?.auth_method && user.auth_method !== 'password') {
    return <SsoInfo authMethod={user.auth_method} />;
  }

  const validation = validatePassword(newPassword);
  const passwordsMatch = newPassword === confirmPassword;
  const sameAsCurrent = isSamePassword(currentPassword, newPassword);
  const canSubmit = isFormReady(
    currentPassword,
    newPassword,
    confirmPassword,
    validation,
    passwordsMatch,
    sameAsCurrent,
    loading
  );

  const clearError = () => setError(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) {
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      await authAPI.changePassword({ old_password: currentPassword, new_password: newPassword });

      setSuccess('Password changed successfully.');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');

      if (forceMode) {
        await refreshUser();
      }
      onSuccess?.();
    } catch (err) {
      setError(formatError(err));
      setCurrentPassword('');
    } finally {
      setLoading(false);
    }
  };

  const formContent = (
    <Box component="form" onSubmit={handleSubmit} noValidate>
      <FormHeader forceMode={forceMode} error={error} success={success} />
      <PasswordFormFields
        currentPassword={currentPassword}
        newPassword={newPassword}
        confirmPassword={confirmPassword}
        onCurrentChange={(e) => {
          setCurrentPassword(e.target.value);
          clearError();
        }}
        onNewChange={(e) => {
          setNewPassword(e.target.value);
          clearError();
        }}
        onConfirmChange={(e) => {
          setConfirmPassword(e.target.value);
          clearError();
        }}
        showCurrentPassword={showCurrentPassword}
        showNewPassword={showNewPassword}
        showConfirmPassword={showConfirmPassword}
        onToggleCurrent={() => setShowCurrentPassword(!showCurrentPassword)}
        onToggleNew={() => setShowNewPassword(!showNewPassword)}
        onToggleConfirm={() => setShowConfirmPassword(!showConfirmPassword)}
        loading={loading}
        sameAsCurrent={sameAsCurrent}
        passwordsMatch={passwordsMatch}
      />
      <SubmitButton loading={loading} disabled={!canSubmit} />
    </Box>
  );

  if (forceMode) {
    return <ForceWrapper onLogout={onLogout}>{formContent}</ForceWrapper>;
  }

  return formContent;
};

export default ChangePasswordForm;
