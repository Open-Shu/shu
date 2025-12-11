import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Grid,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TextField,
  Typography,
  Paper,
  List,
  ListItemIcon,
  ListItemText,
  ListItemButton,
} from '@mui/material';
import { Settings as SettingsIcon } from '@mui/icons-material';
import { useNavigate, useParams, Navigate } from 'react-router-dom';
import { userPreferencesAPI, extractDataFromResponse, formatError } from '../services/api';
import log from '../utils/log';
import NotImplemented from './NotImplemented';
import { useTheme } from '../contexts/ThemeContext';
import { buildUserPreferencesPayload } from '../utils/userPreferences';


export default function UserPreferencesPage() {
  const { themeMode, changeTheme } = useTheme();
  const navigate = useNavigate();
  const { section } = useParams();

  const menuItems = [
    { text: 'General', icon: <SettingsIcon />, path: 'general' },
  ];

  const queryClient = useQueryClient();
  const [error, setError] = useState(null);
  const activeSection = section || 'general';
  const activeItem = menuItems.find((item) => item.path === activeSection);
  const [userPreferences, setUserPreferences] = useState({
    memory_depth: 5,
    memory_similarity_threshold: 0.6,
    theme: themeMode,
    language: 'en',
    timezone: 'UTC',
    advanced_settings: {},
  });

  const updatePreferencesMutation = useMutation(
    (preferences) => userPreferencesAPI.updatePreferences(buildUserPreferencesPayload(preferences)),
    {
      onSuccess: (response) => {
        const updatedPreferences = extractDataFromResponse(response);
        if (updatedPreferences && typeof updatedPreferences === 'object') {
          setUserPreferences((prev) => ({
            ...prev,
            ...updatedPreferences,
            advanced_settings: updatedPreferences.advanced_settings ?? prev.advanced_settings ?? {},
          }));
        }
        queryClient.invalidateQueries('user-preferences');
        setError(null);
      },
      onError: (err) => {
        setError(formatError(err).message);
      }
    }
  );

  const isActive = (sectionKey) => activeSection === sectionKey;

  const handleNavigation = (path) => {
    if (path !== activeSection) {
      navigate(`/settings/preferences/${path}`);
    }
  };

  useQuery(
    'user-preferences',
    userPreferencesAPI.getPreferences,
    {
      onSuccess: (response) => {
        const preferences = extractDataFromResponse(response);
        if (preferences && typeof preferences === 'object') {
          setUserPreferences((prev) => ({
            ...prev,
            ...preferences,
            advanced_settings: preferences.advanced_settings ?? prev.advanced_settings ?? {},
          }));
        }
      },
      onError: (err) => {
        log.warn('Failed to load user preferences:', formatError(err).message);
        // Don't show error to user for preferences - use defaults
      }
    }
  );

  const generalContent = () => (
    <>
      <Typography variant="h6" gutterBottom sx={{ mb: 3 }}>UI Preferences</Typography>

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
                setUserPreferences(prev => ({ ...prev, theme: newTheme }));
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
            value={userPreferences.language}
            onChange={(e) => setUserPreferences(prev => ({ ...prev, language: e.target.value }))}
          />
          <Box sx={{ mt: 0.5 }}>
            <NotImplemented label="Language not applied globally yet" />
          </Box>
        </Grid>
        <Grid item xs={12} sm={4}>
          <TextField
            fullWidth
            label="Timezone"
            value={userPreferences.timezone}
            onChange={(e) => setUserPreferences(prev => ({ ...prev, timezone: e.target.value }))}
          />
          <Box sx={{ mt: 0.5 }}>
            <NotImplemented label="Timezeone not applied globally yet" />
          </Box>
        </Grid>
      </Grid>

      <Box sx={{ mt: 3, display: 'flex', justifyContent: 'flex-end' }}>
        <Button
          onClick={() => updatePreferencesMutation.mutate(userPreferences)}
          variant="contained"
          disabled={updatePreferencesMutation.isLoading}
        >
          {updatePreferencesMutation.isLoading ? 'Saving...' : 'Save Settings'}
        </Button>
      </Box>
    </>
  );

  const getContent = () => {
    if (activeSection === 'general') {
      return generalContent();
    }
    return <Navigate to="/settings/preferences/general" replace />;
  };

  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden' }}>

      {/* Sidebar - Conversations */}
      <Paper
        sx={{
          width: 300,
          minWidth: 300,
          maxWidth: 300,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 0,
          borderRight: 1,
          borderColor: 'divider'
        }}
      >
        <List>
          {menuItems.map((item) => (
            <ListItemButton
              key={item.path}
              selected={isActive(item.path)}
              onClick={() => handleNavigation(item.path)}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.text} />
            </ListItemButton>
          ))}
        </List>
      </Paper>

      {/* Main Content */}
      <Box
        sx={{
          flexGrow: 1,
          bgcolor: 'background.default',
          p: 3,
        }}
      >
        
        <Typography variant="h4" component="h1" gutterBottom>
          {activeItem?.text || 'Preferences'}
        </Typography>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        {/* UI Preferences */}
        <Card>
          <CardContent>
            {getContent()}
          </CardContent>
        </Card>

      </Box>

    </Box>
  );
}
