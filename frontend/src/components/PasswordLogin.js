import React, { useEffect, useState } from 'react';
import { Button, TextField, Box, Typography, Alert, CircularProgress, Paper, Container, Divider } from '@mui/material';
import LoginIcon from '@mui/icons-material/Login';
import GoogleIcon from '@mui/icons-material/Google';
import api, { extractDataFromResponse } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import { getBrandingAppName, getBrandingFaviconUrlForTheme } from '../utils/constants';
import { useMicrosoftOAuth } from '../hooks/useMicrosoftOAuth';

const RESEND_COOLDOWN_SECONDS = 60;

// Backend signals "user must verify email" via this exact message in the
// 400 response detail. SHU-507 keeps the message stable so the frontend
// can offer a resend CTA on this specific failure (and only this one —
// the inactive-account error is structurally similar but unrecoverable
// from the user's side).
const VERIFY_EMAIL_ERROR_MARKER = 'verify your email';

// Microsoft logo - using official asset from Microsoft identity platform branding guidelines
// https://learn.microsoft.com/en-us/entra/identity-platform/howto-add-branding-in-apps
const MicrosoftIcon = () => (
  <img src="/ms-symbollockup_mssymbol_19.svg" alt="" width="20" height="20" style={{ display: 'block' }} />
);

const PasswordLogin = ({
  onSwitchToRegister,
  onSwitchToGoogle,
  onSwitchToForgotPassword,
  isGoogleSsoEnabled = false,
  isMicrosoftSsoEnabled = false,
}) => {
  const [formData, setFormData] = useState({
    email: '',
    password: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);
  // SHU-507: when login fails because the user has not verified their email,
  // surface a "Resend verification email" prompt that uses the address in
  // the form. Without this the user is stuck.
  const [verificationBlocked, setVerificationBlocked] = useState(false);
  const [resendNotice, setResendNotice] = useState(null);
  const [resendCooldown, setResendCooldown] = useState(0);
  const { resendVerification } = useAuth();
  const { branding, resolvedMode } = useAppTheme();
  const appDisplayName = getBrandingAppName(branding);
  const faviconUrl = getBrandingFaviconUrlForTheme(branding, resolvedMode);

  // Microsoft OAuth hook
  const { startLogin: startMicrosoftLogin, loading: microsoftLoading } = useMicrosoftOAuth({
    onSuccess: ({ accessToken, refreshToken }) => {
      localStorage.setItem('shu_token', accessToken);
      if (refreshToken) {
        localStorage.setItem('shu_refresh_token', refreshToken);
      }
      window.location.href = '/';
    },
    onError: (errorMessage) => {
      setError(errorMessage);
    },
    onPendingActivation: () => {
      setSuccessMessage('Your account has been created but requires administrator activation before you can sign in.');
    },
  });

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
    if (error) {
      setError(null);
    }
    if (successMessage) {
      setSuccessMessage(null);
    }
    if (verificationBlocked) {
      setVerificationBlocked(false);
      setResendNotice(null);
    }
  };

  // Tick the resend cooldown so the button re-enables exactly
  // RESEND_COOLDOWN_SECONDS after the most recent click.
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
      // Backend returns the same generic envelope regardless of whether
      // the address exists or is already verified (no enumeration).
      setResendNotice(data?.message || 'If an account is pending verification, a new email has been sent.');
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setResendNotice(err.message || 'Could not resend verification email.');
    }
  };

  const validateForm = () => {
    if (!formData.email || !formData.password) {
      setError('Email and password are required');
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
    setSuccessMessage(null);

    try {
      const response = await api.post('/auth/login/password', {
        email: formData.email,
        password: formData.password,
      });

      const responseData = extractDataFromResponse(response);
      const { access_token, refresh_token } = responseData;

      localStorage.setItem('shu_token', access_token);
      if (refresh_token) {
        localStorage.setItem('shu_refresh_token', refresh_token);
      }

      window.location.href = '/';
    } catch (err) {
      const errorMessage =
        err.response?.data?.error?.message ||
        err.response?.data?.detail ||
        'Login failed. Please check your credentials.';
      setError(errorMessage);
      setVerificationBlocked(errorMessage.toLowerCase().includes(VERIFY_EMAIL_ERROR_MARKER));
    } finally {
      setLoading(false);
    }
  };

  const handleMicrosoftLogin = () => {
    setError(null);
    setSuccessMessage(null);
    startMicrosoftLogin();
  };

  const isAnyLoading = loading || microsoftLoading;

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
        <Paper elevation={3} sx={{ overflow: 'hidden', width: '100%', border: 2, borderColor: 'primary.main' }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: 'primary.main',
              px: 3,
              py: 2,
            }}
          >
            <img src={faviconUrl} alt={appDisplayName} style={{ height: 48, width: 'auto' }} />
          </Box>
          <Box sx={{ textAlign: 'center', p: 4, pt: 3 }}>
            <Typography component="h1" variant="h5" gutterBottom>
              Sign in to {appDisplayName}
            </Typography>
            <Typography variant="body1" color="text.secondary">
              Sign in to your account
            </Typography>
          </Box>

          <Box sx={{ px: 4, pb: 4 }}>
            {error && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {error}
              </Alert>
            )}

            {/* SHU-507: when login was blocked specifically because email is
                not verified, offer a resend CTA. The address comes from the
                form (we already have it) and the backend response is
                non-enumerating (same generic message regardless). */}
            {verificationBlocked && (
              <Box sx={{ mb: 2 }}>
                {resendNotice && (
                  <Alert severity="info" sx={{ mb: 1 }}>
                    {resendNotice}
                  </Alert>
                )}
                <Button
                  fullWidth
                  variant="outlined"
                  onClick={handleResendVerification}
                  disabled={isAnyLoading || !formData.email || resendCooldown > 0}
                >
                  {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Resend verification email'}
                </Button>
              </Box>
            )}

            {successMessage && (
              <Alert severity="success" sx={{ mb: 2 }}>
                {successMessage}
              </Alert>
            )}

            <Box component="form" onSubmit={handleSubmit} sx={{ mt: 1 }}>
              <TextField
                margin="normal"
                required
                fullWidth
                id="email"
                label="Email Address"
                name="email"
                autoComplete="email"
                autoFocus
                type="email"
                value={formData.email}
                onChange={handleChange}
                disabled={isAnyLoading}
              />

              <TextField
                margin="normal"
                required
                fullWidth
                name="password"
                label="Password"
                type="password"
                id="password"
                autoComplete="current-password"
                value={formData.password}
                onChange={handleChange}
                disabled={isAnyLoading}
              />

              <Button
                type="submit"
                fullWidth
                variant="contained"
                size="large"
                startIcon={loading ? <CircularProgress size={20} /> : <LoginIcon />}
                disabled={isAnyLoading}
                sx={{ mt: 3, mb: 2, py: 1.5 }}
              >
                {loading ? 'Signing in...' : 'Sign In'}
              </Button>

              {isGoogleSsoEnabled && (
                <>
                  <Divider sx={{ my: 2 }}>
                    <Typography variant="body2" color="text.secondary">
                      OR
                    </Typography>
                  </Divider>

                  <Button
                    fullWidth
                    variant="outlined"
                    size="large"
                    startIcon={<GoogleIcon />}
                    onClick={onSwitchToGoogle}
                    disabled={isAnyLoading}
                    sx={{ mb: 2, py: 1.5 }}
                  >
                    Sign in with Google
                  </Button>
                </>
              )}

              {isMicrosoftSsoEnabled && (
                <>
                  {!isGoogleSsoEnabled && (
                    <Divider sx={{ my: 2 }}>
                      <Typography variant="body2" color="text.secondary">
                        OR
                      </Typography>
                    </Divider>
                  )}

                  <Button
                    fullWidth
                    variant="outlined"
                    size="large"
                    startIcon={microsoftLoading ? <CircularProgress size={20} /> : <MicrosoftIcon />}
                    onClick={handleMicrosoftLogin}
                    disabled={isAnyLoading}
                    sx={{ mb: 2, py: 1.5 }}
                  >
                    {microsoftLoading ? 'Signing in...' : 'Sign in with Microsoft'}
                  </Button>
                </>
              )}

              <Box sx={{ textAlign: 'center', mt: 2 }}>
                <Button variant="text" onClick={onSwitchToRegister} disabled={isAnyLoading}>
                  Don't have an account? Register
                </Button>
              </Box>
              {onSwitchToForgotPassword && (
                <Box sx={{ textAlign: 'center', mt: 0.5 }}>
                  <Button variant="text" size="small" onClick={onSwitchToForgotPassword} disabled={isAnyLoading}>
                    Forgot password?
                  </Button>
                </Box>
              )}
            </Box>

            <Typography variant="caption" display="block" sx={{ textAlign: 'center', mt: 2 }}>
              Only authorized {appDisplayName} accounts can access this system
            </Typography>
          </Box>
        </Paper>
      </Box>
    </Container>
  );
};

export default PasswordLogin;
