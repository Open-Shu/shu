import React, { useEffect, useState } from 'react';
import { Button, TextField, Box, Typography, Alert, CircularProgress, Paper, Container, Stack } from '@mui/material';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import { useAuth } from '../hooks/useAuth';
import api, { extractDataFromResponse } from '../services/api';
import log from '../utils/log';

const RESEND_COOLDOWN_SECONDS = 60;

const PasswordRegistration = ({ onSwitchToLogin }) => {
  const { resendVerification } = useAuth();
  const [formData, setFormData] = useState({
    email: '',
    password: '',
    confirmPassword: '',
    name: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // SHU-507: backend returns one of four statuses driving the success screen.
  // null = form, otherwise "activated" | "pending_email_verification" |
  // "pending_admin_activation" | "pending_verification_and_admin".
  const [registrationStatus, setRegistrationStatus] = useState(null);
  const [resendCooldown, setResendCooldown] = useState(0);
  const [resendNotice, setResendNotice] = useState(null);

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
    // Clear error when user starts typing
    if (error) {
      setError(null);
    }
  };

  const validateForm = () => {
    if (!formData.email || !formData.password || !formData.name) {
      setError('All fields are required');
      return false;
    }

    if (formData.password !== formData.confirmPassword) {
      setError('Passwords do not match');
      return false;
    }

    if (formData.password.length < 8) {
      setError('Password must be at least 8 characters long');
      return false;
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(formData.email)) {
      setError('Please enter a valid email address');
      return false;
    }

    return true;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!validateForm()) {
      return;
    }

    setLoading(true);
    setError(null);

    try {
      // Register the user
      const response = await api.post('/auth/register', {
        email: formData.email,
        password: formData.password,
        name: formData.name,
      });

      const data = extractDataFromResponse(response);
      const knownStatuses = new Set([
        'activated',
        'pending_email_verification',
        'pending_admin_activation',
        'pending_verification_and_admin',
      ]);
      if (!knownStatuses.has(data?.status)) {
        log.warn('Unexpected register response payload', data);
      }
      setRegistrationStatus(data?.status || 'pending_admin_activation');
    } catch (err) {
      const errorMessage =
        err.response?.data?.error?.message || err.response?.data?.detail || 'Registration failed. Please try again.';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  // Decrement resend cooldown each second so the button re-enables exactly
  // RESEND_COOLDOWN_SECONDS after the most recent click. Backend rate-limits
  // are the source of truth (3/hour); the cooldown just discourages
  // hammering between legitimate retries.
  useEffect(() => {
    if (resendCooldown <= 0) {
      return undefined;
    }
    const id = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000);
    return () => clearTimeout(id);
  }, [resendCooldown]);

  const handleResendVerification = async () => {
    setResendNotice(null);
    try {
      const data = await resendVerification(formData.email);
      // Backend always returns the same generic envelope (no enumeration).
      setResendNotice(data?.message || 'If an account is pending verification, a new email has been sent.');
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setResendNotice(err.message || 'Could not resend verification email.');
    }
  };

  if (registrationStatus) {
    const isVerificationPending = registrationStatus === 'pending_email_verification';
    const isAdminPending = registrationStatus === 'pending_admin_activation';
    const isVerificationAndAdminPending = registrationStatus === 'pending_verification_and_admin';
    const isActivated = registrationStatus === 'activated';

    return (
      <Container maxWidth="sm">
        <Box
          sx={{
            marginTop: 8,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
          }}
        >
          <Paper elevation={3} sx={{ padding: 4, width: '100%' }}>
            <Box sx={{ textAlign: 'center' }}>
              <Typography component="h1" variant="h4" gutterBottom color="success.main">
                Registration Successful!
              </Typography>
              {isVerificationPending && (
                <>
                  <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
                    Check your inbox for a verification link sent to <strong>{formData.email}</strong>. Click the link
                    to activate your account before logging in.
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    The link expires in 24 hours. Did not get an email? Check your spam folder, or request a new one
                    below.
                  </Typography>
                  {resendNotice && (
                    <Alert severity="info" sx={{ mb: 2 }}>
                      {resendNotice}
                    </Alert>
                  )}
                </>
              )}
              {isAdminPending && (
                <>
                  <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
                    Your account has been created but requires administrator activation before you can log in.
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                    Please contact your administrator to activate your account.
                  </Typography>
                </>
              )}
              {isVerificationAndAdminPending && (
                <>
                  <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
                    Check your inbox for a verification link sent to <strong>{formData.email}</strong>.
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Once your email is verified, an administrator still needs to activate your account before you can
                    sign in. You will not be able to log in until both steps are complete.
                  </Typography>
                  {resendNotice && (
                    <Alert severity="info" sx={{ mb: 2 }}>
                      {resendNotice}
                    </Alert>
                  )}
                </>
              )}
              {isActivated && (
                <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
                  Your account is active. You can sign in now.
                </Typography>
              )}

              {/*
                Action buttons share a row with equal widths so neither
                dominates visually. In the verification-pending state,
                "Resend" is the primary action (most likely next step for
                a user who just registered) and "Return to Login" is the
                secondary escape hatch. In the other states there is no
                resend, and the single button stretches to fill the row.
              */}
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ mt: 2 }}>
                {(isVerificationPending || isVerificationAndAdminPending) && (
                  <Button
                    variant="contained"
                    onClick={handleResendVerification}
                    disabled={resendCooldown > 0}
                    fullWidth
                  >
                    {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Resend verification email'}
                  </Button>
                )}
                <Button
                  variant={isVerificationPending || isVerificationAndAdminPending ? 'outlined' : 'contained'}
                  onClick={onSwitchToLogin}
                  fullWidth
                >
                  Return to Login
                </Button>
              </Stack>
            </Box>
          </Paper>
        </Box>
      </Container>
    );
  }

  return (
    <Container maxWidth="sm">
      <Box
        sx={{
          marginTop: 8,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
        }}
      >
        <Paper elevation={3} sx={{ padding: 4, width: '100%' }}>
          <Box sx={{ textAlign: 'center', mb: 3 }}>
            <Typography component="h1" variant="h4" gutterBottom>
              Create Account
            </Typography>
            <Typography variant="body1" color="text.secondary">
              Register for Shu Admin Console
            </Typography>
          </Box>

          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit} sx={{ mt: 1 }}>
            <TextField
              margin="normal"
              required
              fullWidth
              id="name"
              label="Full Name"
              name="name"
              autoComplete="name"
              autoFocus
              value={formData.name}
              onChange={handleChange}
              disabled={loading}
            />

            <TextField
              margin="normal"
              required
              fullWidth
              id="email"
              label="Email Address"
              name="email"
              autoComplete="email"
              type="email"
              value={formData.email}
              onChange={handleChange}
              disabled={loading}
            />

            <TextField
              margin="normal"
              required
              fullWidth
              name="password"
              label="Password"
              type="password"
              id="password"
              autoComplete="new-password"
              value={formData.password}
              onChange={handleChange}
              disabled={loading}
              helperText="Password must be at least 8 characters long"
            />

            <TextField
              margin="normal"
              required
              fullWidth
              name="confirmPassword"
              label="Confirm Password"
              type="password"
              id="confirmPassword"
              value={formData.confirmPassword}
              onChange={handleChange}
              disabled={loading}
            />

            <Button
              type="submit"
              fullWidth
              variant="contained"
              size="large"
              startIcon={loading ? <CircularProgress size={20} /> : <PersonAddIcon />}
              disabled={loading}
              sx={{ mt: 3, mb: 2, py: 1.5 }}
            >
              {loading ? 'Creating Account...' : 'Create Account'}
            </Button>

            <Box sx={{ textAlign: 'center', mt: 2 }}>
              <Button variant="text" onClick={onSwitchToLogin} disabled={loading}>
                Already have an account? Sign in
              </Button>
            </Box>
          </Box>
        </Paper>
      </Box>
    </Container>
  );
};

export default PasswordRegistration;
