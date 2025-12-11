import React, { useState } from 'react';
import {
  Button,
  TextField,
  Box,
  Typography,
  Alert,
  CircularProgress,
  Paper,
  Container,
  Divider
} from '@mui/material';
import LoginIcon from '@mui/icons-material/Login';
import GoogleIcon from '@mui/icons-material/Google';
import api, { extractDataFromResponse } from '../services/api';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import { getBrandingAppName } from '../utils/constants';

const PasswordLogin = ({ onSwitchToRegister, onSwitchToGoogle, isGoogleSsoEnabled = false }) => {
  const [formData, setFormData] = useState({
    email: '',
    password: ''
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const { branding } = useAppTheme();
  const appDisplayName = getBrandingAppName(branding);
  const logoUrl = branding?.logoUrl;

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value
    });
    // Clear error when user starts typing
    if (error) setError(null);
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

    try {
      // Login with password
      const response = await api.post('/auth/login/password', {
        email: formData.email,
        password: formData.password
      });

      const responseData = extractDataFromResponse(response);
      const { access_token, refresh_token } = responseData;

      // Store tokens
      localStorage.setItem('shu_token', access_token);
      localStorage.setItem('shu_refresh_token', refresh_token);

      // Redirect to main app (the auth wrapper will handle the redirect)
      window.location.href = '/';

    } catch (err) {
      const errorMessage = err.response?.data?.error?.message || 
                          err.response?.data?.detail || 
                          'Login failed. Please check your credentials.';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

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
            <img
              src={logoUrl}
              alt={appDisplayName}
              style={{
                height: '60px', // Fixed height for normal proportions
                width: 'auto', // Maintain aspect ratio
                maxWidth: '100%', // Don't exceed container width
                marginBottom: '1rem',
              }}
            />
            <Typography component="h1" variant="h4" gutterBottom>
              Sign-in to {appDisplayName}
            </Typography>
            <Typography variant="body1" color="text.secondary">
              Sign in to your account
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
              id="email"
              label="Email Address"
              name="email"
              autoComplete="email"
              autoFocus
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
              autoComplete="current-password"
              value={formData.password}
              onChange={handleChange}
              disabled={loading}
            />

            <Button
              type="submit"
              fullWidth
              variant="contained"
              size="large"
              startIcon={loading ? <CircularProgress size={20} /> : <LoginIcon />}
              disabled={loading}
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
                  disabled={loading}
                  sx={{ mb: 2, py: 1.5 }}
                >
                  Sign in with Google
                </Button>
              </>
            )}

            <Box sx={{ textAlign: 'center', mt: 2 }}>
              <Button
                variant="text"
                onClick={onSwitchToRegister}
                disabled={loading}
              >
                Don't have an account? Register
              </Button>
            </Box>
          </Box>

          <Typography variant="caption" display="block" sx={{ textAlign: 'center', mt: 2 }}>
            Only authorized {appDisplayName} accounts can access this system
          </Typography>
        </Paper>
      </Box>
    </Container>
  );
};

export default PasswordLogin;
