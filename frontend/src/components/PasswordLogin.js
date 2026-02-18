import React, { useState } from 'react';
import { Button, TextField, Box, Typography, Alert, CircularProgress, Paper, Container, Divider } from '@mui/material';
import LoginIcon from '@mui/icons-material/Login';
import GoogleIcon from '@mui/icons-material/Google';
import api, { extractDataFromResponse } from '../services/api';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import { getBrandingAppName, getBrandingFaviconUrlForTheme } from '../utils/constants';
import { useMicrosoftOAuth } from '../hooks/useMicrosoftOAuth';

// Microsoft logo - using official asset from Microsoft identity platform branding guidelines
// https://learn.microsoft.com/en-us/entra/identity-platform/howto-add-branding-in-apps
const MicrosoftIcon = () => (
  <img src="/ms-symbollockup_mssymbol_19.svg" alt="" width="20" height="20" style={{ display: 'block' }} />
);

const PasswordLogin = ({
  onSwitchToRegister,
  onSwitchToGoogle,
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
