import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Alert, Box, Button, CircularProgress, Container, Paper, Stack, TextField, Typography } from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import { useAuth } from '../hooks/useAuth';

const RESEND_COOLDOWN_SECONDS = 60;
const EXPIRED_TOKEN_CODE = 'PASSWORD_RESET_TOKEN_EXPIRED';

/**
 * SHU-745 password-reset landing page.
 *
 * The reset email contains a link of the form
 * `{app_base_url}/reset-password?token=...`. This page reads the token
 * from the query string, prompts for a new password, and posts both to
 * the backend. The token is hashed server-side and matched against the
 * `password_reset_token` table.
 *
 * Recovery paths from a failed reset:
 *
 * - **Expired token (we know who they are)**: token-based "Send a new
 *   reset link" button. The same expired token is handed back to the
 *   server, which resolves the user from its hash and issues a fresh
 *   token. The user never has to type, see, or know their email.
 * - **Unknown / missing token (we cannot identify the user)**: link
 *   back to the "Forgot password" entry on the sign-in page.
 *
 * StrictMode note: this page does NOT auto-submit on mount (unlike the
 * SHU-507 verify page). The user must enter a new password and click
 * Submit, so React 18's double-effect doesn't trigger duplicate API
 * calls. No promise-caching ref is required.
 */
const ResetPasswordPage = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { resetPassword, resendPasswordResetFromToken } = useAuth();

  const token = searchParams.get('token');
  // Page state machine. 'form' = ready for input; 'submitting' = network
  // in flight; 'success' / 'expired' / 'missing' = terminal. Token-invalid
  // and policy-violation errors stay on 'form' with an inline `formError`
  // so the user can either retype a stronger password or click "Cancel"
  // to return to login (the backend doesn't currently emit a structured
  // PASSWORD_RESET_TOKEN_INVALID code; if we add one later, route it to a
  // dedicated 'invalid' branch).
  const [state, setState] = useState(token ? 'form' : 'missing');

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [formError, setFormError] = useState(null);

  const [resendCooldown, setResendCooldown] = useState(0);
  const [resendNotice, setResendNotice] = useState(null);

  // Tick the resend cooldown so the button re-enables exactly
  // RESEND_COOLDOWN_SECONDS after the most recent click.
  useEffect(() => {
    if (resendCooldown <= 0) {
      return undefined;
    }
    const id = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000);
    return () => clearTimeout(id);
  }, [resendCooldown]);

  const goToLogin = () => navigate('/auth');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setFormError(null);

    if (!newPassword || !confirmPassword) {
      setFormError('Please enter and confirm your new password.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setFormError('Passwords do not match.');
      return;
    }

    setState('submitting');
    try {
      await resetPassword(token, newPassword);
      setState('success');
    } catch (err) {
      if (err.code === EXPIRED_TOKEN_CODE) {
        setState('expired');
      } else {
        // Includes policy errors ("Password must contain...") and the
        // generic "reset token is invalid" string. Surface inline on the
        // form so the user can either fix the password and retry, or
        // click Cancel to start a new request from the login page.
        setFormError(err.message);
        setState('form');
      }
    }
  };

  const handleTokenResend = async () => {
    setResendNotice(null);
    try {
      const data = await resendPasswordResetFromToken(token);
      setResendNotice(data?.message || 'A new reset link has been sent.');
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setResendNotice(err.message || 'Could not resend reset email.');
    }
  };

  return (
    <Container maxWidth="sm">
      <Box sx={{ marginTop: 8, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <Paper elevation={3} sx={{ padding: 4, width: '100%' }}>
          <Box sx={{ textAlign: 'center' }}>
            {(state === 'form' || state === 'submitting') && (
              <>
                <Typography component="h1" variant="h5" gutterBottom>
                  Choose a new password
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                  Pick a password you haven&apos;t used here before. After this reset, every other device signed into
                  your account will be signed out.
                </Typography>
                {formError && (
                  <Alert severity="error" sx={{ mb: 2, textAlign: 'left' }}>
                    {formError}
                  </Alert>
                )}
                <Box component="form" onSubmit={handleSubmit}>
                  <TextField
                    label="New password"
                    type="password"
                    fullWidth
                    autoFocus
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                    autoComplete="new-password"
                    sx={{ mb: 2 }}
                    disabled={state === 'submitting'}
                  />
                  <TextField
                    label="Confirm new password"
                    type="password"
                    fullWidth
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    autoComplete="new-password"
                    sx={{ mb: 2 }}
                    disabled={state === 'submitting'}
                  />
                  <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                    <Button
                      type="submit"
                      variant="contained"
                      fullWidth
                      disabled={state === 'submitting'}
                      startIcon={state === 'submitting' ? <CircularProgress size={18} /> : null}
                    >
                      {state === 'submitting' ? 'Resetting…' : 'Reset password'}
                    </Button>
                    <Button variant="outlined" onClick={goToLogin} fullWidth disabled={state === 'submitting'}>
                      Cancel
                    </Button>
                  </Stack>
                </Box>
              </>
            )}
            {state === 'success' && (
              <>
                <CheckCircleIcon sx={{ fontSize: 56, color: 'success.main', mb: 2 }} />
                <Typography component="h1" variant="h5" gutterBottom>
                  Password reset
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                  Your password has been updated. Sign in with your new password to continue.
                </Typography>
                <Button variant="contained" onClick={goToLogin}>
                  Go to sign in
                </Button>
              </>
            )}
            {state === 'expired' && (
              <>
                <ErrorIcon sx={{ fontSize: 56, color: 'warning.main', mb: 2 }} />
                <Typography component="h1" variant="h5" gutterBottom>
                  Reset link expired
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Click below and we&apos;ll send a fresh reset link to the address on file for this account.
                </Typography>
                {resendNotice && (
                  <Alert severity="info" sx={{ mb: 2 }}>
                    {resendNotice}
                  </Alert>
                )}
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                  <Button variant="contained" onClick={handleTokenResend} disabled={resendCooldown > 0} fullWidth>
                    {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Send a new reset link'}
                  </Button>
                  <Button variant="outlined" onClick={goToLogin} fullWidth>
                    Go to sign in
                  </Button>
                </Stack>
              </>
            )}
            {state === 'missing' && (
              <>
                <ErrorIcon sx={{ fontSize: 56, color: 'warning.main', mb: 2 }} />
                <Typography component="h1" variant="h5" gutterBottom>
                  No reset token found
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Open the link from your password reset email, or request a new one from the sign-in screen.
                </Typography>
                <Button variant="contained" onClick={goToLogin}>
                  Go to sign in
                </Button>
              </>
            )}
          </Box>
        </Paper>
      </Box>
    </Container>
  );
};

export default ResetPasswordPage;
