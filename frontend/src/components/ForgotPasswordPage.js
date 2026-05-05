import React, { useEffect, useState } from 'react';
import { Alert, Box, Button, CircularProgress, Container, Paper, Stack, TextField, Typography } from '@mui/material';
import { useAuth } from '../hooks/useAuth';

const RESEND_COOLDOWN_SECONDS = 60;

/**
 * SHU-745 password-reset request page.
 *
 * Reached from the "Forgot password?" link on the password sign-in form.
 * Posts an email address to the backend; the backend returns the same
 * generic envelope regardless of whether the address exists, is SSO-only,
 * is inactive, or hits the rate limit (no enumeration).
 */
const ForgotPasswordPage = ({ onSwitchToLogin }) => {
  const { requestPasswordReset } = useAuth();
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitNotice, setSubmitNotice] = useState(null);
  const [resendCooldown, setResendCooldown] = useState(0);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (resendCooldown <= 0) {
      return undefined;
    }
    const id = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000);
    return () => clearTimeout(id);
  }, [resendCooldown]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setSubmitNotice(null);

    if (!email) {
      setError('Enter your email address.');
      return;
    }
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
      setError('Please enter a valid email address.');
      return;
    }

    setSubmitting(true);
    try {
      const data = await requestPasswordReset(email);
      // Generic envelope — no distinction between known/unknown/etc.
      setSubmitNotice(
        data?.message || 'If an account is registered for this address, a password reset email has been sent.'
      );
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setError(err.message || 'Could not request a password reset.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Container maxWidth="sm">
      <Box sx={{ marginTop: 8, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <Paper elevation={3} sx={{ padding: 4, width: '100%' }}>
          <Box sx={{ textAlign: 'center' }}>
            <Typography component="h1" variant="h5" gutterBottom>
              Forgot your password?
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
              Enter the email address you used to register and we&apos;ll send you a link to set a new password.
            </Typography>
            {error && (
              <Alert severity="error" sx={{ mb: 2, textAlign: 'left' }}>
                {error}
              </Alert>
            )}
            {submitNotice && (
              <Alert severity="info" sx={{ mb: 2, textAlign: 'left' }}>
                {submitNotice}
              </Alert>
            )}
            <Box component="form" onSubmit={handleSubmit}>
              <TextField
                label="Email address"
                type="email"
                fullWidth
                autoFocus
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                sx={{ mb: 2 }}
                disabled={submitting}
              />
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <Button
                  type="submit"
                  variant="contained"
                  fullWidth
                  disabled={submitting || resendCooldown > 0}
                  startIcon={submitting ? <CircularProgress size={18} /> : null}
                >
                  {submitting ? 'Sending…' : resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Send reset link'}
                </Button>
                <Button variant="outlined" fullWidth onClick={onSwitchToLogin} disabled={submitting}>
                  Back to sign in
                </Button>
              </Stack>
            </Box>
          </Box>
        </Paper>
      </Box>
    </Container>
  );
};

export default ForgotPasswordPage;
