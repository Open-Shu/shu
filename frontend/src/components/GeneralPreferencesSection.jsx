import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
  Alert,
  Box,
  Button,
  Grid,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TextField,
  Typography,
} from '@mui/material';
import { userPreferencesAPI, extractDataFromResponse, formatError } from '../services/api';
import log from '../utils/log';
import NotImplemented from './NotImplemented';
import { useTheme } from '../contexts/ThemeContext';
import { buildUserPreferencesPayload } from '../utils/userPreferences';

function useUserPreferences(themeMode) {
  const queryClient = useQueryClient();
  const [error, setError] = useState(null);
  const [preferences, setPreferences] = useState({
    memory_depth: 5,
    memory_similarity_threshold: 0.6,
    theme: themeMode,
    language: 'en',
    timezone: 'UTC',
    advanced_settings: {},
  });

  const mutation = useMutation((prefs) => userPreferencesAPI.updatePreferences(buildUserPreferencesPayload(prefs)), {
    onSuccess: (response) => {
      const updated = extractDataFromResponse(response);
      if (updated && typeof updated === 'object') {
        setPreferences((prev) => ({
          ...prev,
          ...updated,
          advanced_settings: updated.advanced_settings ?? prev.advanced_settings ?? {},
        }));
      }
      queryClient.invalidateQueries('user-preferences');
      setError(null);
    },
    onError: (err) => setError(formatError(err)),
  });

  useQuery('user-preferences', userPreferencesAPI.getPreferences, {
    onSuccess: (response) => {
      const data = extractDataFromResponse(response);
      if (data && typeof data === 'object') {
        setPreferences((prev) => ({
          ...prev,
          ...data,
          advanced_settings: data.advanced_settings ?? prev.advanced_settings ?? {},
        }));
      }
    },
    onError: (err) => log.warn('Failed to load user preferences:', formatError(err)),
  });

  return { preferences, setPreferences, error, setError, mutation };
}

export default function GeneralPreferencesSection() {
  const { themeMode, changeTheme } = useTheme();
  const { preferences, setPreferences, error, setError, mutation } = useUserPreferences(themeMode);

  return (
    <>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <Typography variant="h6" gutterBottom sx={{ mb: 3 }}>
        UI Preferences
      </Typography>

      <Grid container spacing={3}>
        <Grid item xs={12} sm={4}>
          <FormControl fullWidth>
            <InputLabel>Theme</InputLabel>
            <Select
              value={themeMode}
              label="Theme"
              onChange={(e) => {
                const newTheme = e.target.value;
                changeTheme(newTheme);
                setPreferences((prev) => ({ ...prev, theme: newTheme }));
              }}
            >
              <MenuItem value="light">Light</MenuItem>
              <MenuItem value="dark">Dark</MenuItem>
              <MenuItem value="auto">Auto (System)</MenuItem>
            </Select>
          </FormControl>
        </Grid>
        <Grid item xs={12} sm={4}>
          <TextField
            fullWidth
            label="Language"
            value={preferences.language}
            onChange={(e) =>
              setPreferences((prev) => ({
                ...prev,
                language: e.target.value,
              }))
            }
          />
          <Box sx={{ mt: 0.5 }}>
            <NotImplemented label="Language not applied globally yet" />
          </Box>
        </Grid>
        <Grid item xs={12} sm={4}>
          <TextField
            fullWidth
            label="Timezone"
            value={preferences.timezone}
            onChange={(e) =>
              setPreferences((prev) => ({
                ...prev,
                timezone: e.target.value,
              }))
            }
          />
          <Box sx={{ mt: 0.5 }}>
            <NotImplemented label="Timezone not applied globally yet" />
          </Box>
        </Grid>
      </Grid>

      <Box sx={{ mt: 3, display: 'flex', justifyContent: 'flex-end' }}>
        <Button onClick={() => mutation.mutate(preferences)} variant="contained" disabled={mutation.isLoading}>
          {mutation.isLoading ? 'Saving...' : 'Save Settings'}
        </Button>
      </Box>
    </>
  );
}
