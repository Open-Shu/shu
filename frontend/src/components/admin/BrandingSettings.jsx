import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Box, Button, CircularProgress, Grid, Paper, Stack, TextField, Typography } from '@mui/material';

import PaletteIcon from '@mui/icons-material/Palette';
import { brandingAPI, extractDataFromResponse, formatError } from '../../services/api';
import { useTheme as useAppTheme } from '../../contexts/ThemeContext';
import { getThemeConfig } from '../../utils/constants';
import log from '../../utils/log';
import PageHelpHeader from '../PageHelpHeader';

const emptyForm = {
  appName: '',
  faviconUrl: '',
  darkFaviconUrl: '',
  lightTopbarTextColor: '#FFFFFF',
  darkTopbarTextColor: '#FFFFFF',
  light: {
    primaryMain: '',
    secondaryMain: '',
    backgroundDefault: '',
  },
  dark: {
    primaryMain: '',
    secondaryMain: '',
    backgroundDefault: '',
  },
};

const buildPaletteOverrides = (sectionState) => {
  const palette = {};

  if (sectionState.primaryMain) {
    palette.primary = { main: sectionState.primaryMain };
  }
  if (sectionState.secondaryMain) {
    palette.secondary = { main: sectionState.secondaryMain };
  }
  if (sectionState.backgroundDefault) {
    palette.background = { default: sectionState.backgroundDefault };
  }

  return Object.keys(palette).length > 0 ? { palette } : null;
};

const toNullable = (value) => {
  if (value === undefined || value === null) {
    return null;
  }
  const trimmed = String(value).trim();
  return trimmed.length === 0 ? null : trimmed;
};

const BrandingSettings = () => {
  const { branding, setBranding, brandingLoaded } = useAppTheme();
  const [formState, setFormState] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState(null);
  const [uploadingLightFavicon, setUploadingLightFavicon] = useState(false);
  const [uploadingDarkFavicon, setUploadingDarkFavicon] = useState(false);

  const lightFaviconInputRef = useRef(null);
  const darkFaviconInputRef = useRef(null);

  const resolvedLightTheme = useMemo(() => getThemeConfig('light', branding), [branding]);
  const resolvedDarkTheme = useMemo(() => getThemeConfig('dark', branding), [branding]);

  // Initialize form state only once when branding is first loaded
  useEffect(() => {
    if (!brandingLoaded) {
      return;
    }

    setFormState({
      appName: branding.appName || '',
      faviconUrl: branding.faviconUrl || '',
      darkFaviconUrl: branding.darkFaviconUrl || '',
      lightTopbarTextColor: branding.lightTopbarTextColor || '#FFFFFF',
      darkTopbarTextColor: branding.darkTopbarTextColor || '#FFFFFF',
      light: {
        primaryMain: branding.lightThemeOverrides?.palette?.primary?.main || resolvedLightTheme.palette.primary.main,
        secondaryMain:
          branding.lightThemeOverrides?.palette?.secondary?.main || resolvedLightTheme.palette.secondary.main,
        backgroundDefault:
          branding.lightThemeOverrides?.palette?.background?.default || resolvedLightTheme.palette.background.default,
      },
      dark: {
        primaryMain: branding.darkThemeOverrides?.palette?.primary?.main || resolvedDarkTheme.palette.primary.main,
        secondaryMain:
          branding.darkThemeOverrides?.palette?.secondary?.main || resolvedDarkTheme.palette.secondary.main,
        backgroundDefault:
          branding.darkThemeOverrides?.palette?.background?.default || resolvedDarkTheme.palette.background.default,
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brandingLoaded]); // Only run when brandingLoaded changes from false to true

  // Update form state when branding changes from uploads (but not from user typing)
  useEffect(() => {
    if (!brandingLoaded) {
      return;
    }

    // Only update the asset URLs that come from uploads, not the text fields or colors
    setFormState((prev) => ({
      ...prev,
      faviconUrl: branding.faviconUrl || prev.faviconUrl,
      darkFaviconUrl: branding.darkFaviconUrl || prev.darkFaviconUrl,
    }));
  }, [branding.faviconUrl, branding.darkFaviconUrl, brandingLoaded]);

  const handleTextChange = (field) => (event) => {
    const { value } = event.target;
    setFormState((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const handleColorChange = (mode, field) => (event) => {
    const { value } = event.target;
    setFormState((prev) => ({
      ...prev,
      [mode]: {
        ...prev[mode],
        [field]: value,
      },
    }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setStatus(null);
    setSaving(true);

    const payload = {
      app_name: toNullable(formState.appName),
      favicon_url: toNullable(formState.faviconUrl),
      dark_favicon_url: toNullable(formState.darkFaviconUrl),
      light_topbar_text_color: toNullable(formState.lightTopbarTextColor),
      dark_topbar_text_color: toNullable(formState.darkTopbarTextColor),
      light_theme_overrides: buildPaletteOverrides(formState.light),
      dark_theme_overrides: buildPaletteOverrides(formState.dark),
    };

    try {
      const response = await brandingAPI.updateBranding(payload);
      const data = extractDataFromResponse(response);
      setBranding(data);
      setStatus({ type: 'success', message: 'Branding updated successfully.' });
    } catch (error) {
      const message = formatError(error);
      log.error('Branding update failed', error);
      setStatus({ type: 'error', message });
    } finally {
      setSaving(false);
    }
  };

  const handleResetBranding = async () => {
    setStatus(null);
    setSaving(true);
    try {
      const response = await brandingAPI.updateBranding({
        app_name: null,
        favicon_url: null,
        dark_favicon_url: null,
        light_topbar_text_color: null,
        dark_topbar_text_color: null,
        light_theme_overrides: null,
        dark_theme_overrides: null,
      });
      const data = extractDataFromResponse(response);
      setBranding(data);

      // Resolve themes with the reset data
      const newLightTheme = getThemeConfig('light', data);
      const newDarkTheme = getThemeConfig('dark', data);

      // Update form state with the reset values
      setFormState({
        appName: data.appName || '',
        faviconUrl: data.faviconUrl || '',
        darkFaviconUrl: data.darkFaviconUrl || '',
        lightTopbarTextColor: data.lightTopbarTextColor || '#FFFFFF',
        darkTopbarTextColor: data.darkTopbarTextColor || '#FFFFFF',
        light: {
          primaryMain: data.lightThemeOverrides?.palette?.primary?.main || newLightTheme.palette.primary.main,
          secondaryMain: data.lightThemeOverrides?.palette?.secondary?.main || newLightTheme.palette.secondary.main,
          backgroundDefault:
            data.lightThemeOverrides?.palette?.background?.default || newLightTheme.palette.background.default,
        },
        dark: {
          primaryMain: data.darkThemeOverrides?.palette?.primary?.main || newDarkTheme.palette.primary.main,
          secondaryMain: data.darkThemeOverrides?.palette?.secondary?.main || newDarkTheme.palette.secondary.main,
          backgroundDefault:
            data.darkThemeOverrides?.palette?.background?.default || newDarkTheme.palette.background.default,
        },
      });

      setStatus({ type: 'success', message: 'Branding reset to defaults.' });
    } catch (error) {
      const message = formatError(error);
      log.error('Branding reset failed', error);
      setStatus({ type: 'error', message });
    } finally {
      setSaving(false);
    }
  };

  const uploadFavicon = async (theme, file) => {
    if (!file) {
      return;
    }

    setStatus(null);

    if (theme === 'light') {
      setUploadingLightFavicon(true);
    } else {
      setUploadingDarkFavicon(true);
    }

    try {
      const response = await brandingAPI.uploadFavicon(file, theme);
      const data = extractDataFromResponse(response);
      setBranding(data);
      setStatus({
        type: 'success',
        message: `${theme === 'light' ? 'Light' : 'Dark'} mode favicon updated.`,
      });
    } catch (error) {
      const message = formatError(error);
      log.error('Favicon upload failed', error);
      setStatus({ type: 'error', message });
    } finally {
      if (theme === 'light' && lightFaviconInputRef.current) {
        lightFaviconInputRef.current.value = '';
      }
      if (theme === 'dark' && darkFaviconInputRef.current) {
        darkFaviconInputRef.current.value = '';
      }
      setUploadingLightFavicon(false);
      setUploadingDarkFavicon(false);
    }
  };

  if (!brandingLoaded) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="60vh">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box component="form" onSubmit={handleSubmit} sx={{ maxWidth: 1000, mx: 'auto' }}>
      <Stack spacing={3}>
        <PageHelpHeader
          title="Branding Settings"
          description="Customize the look and feel of your application. Set a favicon, and configure color themes for both light and dark modes."
          icon={<PaletteIcon />}
          tips={[
            'The favicon appears in browser tabs and the topbar—upload a small square image',
            'Set primary colors to match your brand identity',
            'Configure both light and dark mode themes for users who prefer either',
            'Changes take effect immediately after saving',
          ]}
        />

        {status && (
          <Alert severity={status.type} onClose={() => setStatus(null)}>
            {status.message}
          </Alert>
        )}

        <Paper sx={{ p: 3 }}>
          <Stack spacing={2}>
            <Typography variant="h6" fontWeight={600}>
              General
            </Typography>
            <TextField
              label="Application Name"
              value={formState.appName}
              onChange={handleTextChange('appName')}
              fullWidth
            />
          </Stack>
        </Paper>

        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ width: '100%' }}>
          <Paper sx={{ p: 3, flex: 1 }}>
            <Stack spacing={2}>
              <Stack spacing={1.5}>
                <Typography variant="subtitle1" fontWeight={600}>
                  Light Mode Favicon
                </Typography>
                <Box
                  sx={{
                    backgroundColor: resolvedLightTheme.palette.primary.main,
                    padding: 2,
                    borderRadius: 1,
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    minHeight: 100,
                  }}
                >
                  <img
                    src={branding.faviconUrl}
                    alt="Light mode favicon preview"
                    style={{ height: 80, objectFit: 'contain' }}
                  />
                </Box>
                <Stack spacing={1}>
                  <Button
                    variant="outlined"
                    onClick={() => lightFaviconInputRef.current?.click()}
                    disabled={uploadingLightFavicon}
                    fullWidth
                  >
                    {uploadingLightFavicon ? 'Uploading…' : 'Upload Light Favicon'}
                  </Button>
                  <input
                    value={formState.faviconUrl}
                    onChange={handleTextChange('faviconUrl')}
                    placeholder="https://example.com/favicon.png"
                    type="hidden"
                  />
                </Stack>
                <input
                  ref={lightFaviconInputRef}
                  type="file"
                  accept=".svg,.png,.ico,.webp"
                  style={{ display: 'none' }}
                  onChange={(event) => uploadFavicon('light', event.target.files?.[0])}
                />
              </Stack>
            </Stack>
          </Paper>
          <Paper sx={{ p: 3, flex: 1 }}>
            <Stack spacing={2}>
              <Stack spacing={1.5}>
                <Typography variant="subtitle1" fontWeight={600}>
                  Dark Mode Favicon
                </Typography>
                <Box
                  sx={{
                    backgroundColor: resolvedDarkTheme.palette.primary.main,
                    padding: 2,
                    borderRadius: 1,
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    minHeight: 100,
                  }}
                >
                  <img
                    src={branding.darkFaviconUrl || branding.faviconUrl}
                    alt="Dark mode favicon preview"
                    style={{ height: 80, objectFit: 'contain' }}
                  />
                </Box>
                {!branding.darkFaviconUrl && (
                  <Typography variant="caption" color="text.secondary">
                    Using light mode favicon as fallback
                  </Typography>
                )}
                <Stack spacing={1}>
                  <Button
                    variant="outlined"
                    onClick={() => darkFaviconInputRef.current?.click()}
                    disabled={uploadingDarkFavicon}
                    fullWidth
                  >
                    {uploadingDarkFavicon ? 'Uploading…' : 'Upload Dark Favicon'}
                  </Button>
                  <input
                    value={formState.darkFaviconUrl}
                    onChange={handleTextChange('darkFaviconUrl')}
                    placeholder="https://example.com/dark-favicon.png"
                    type="hidden"
                  />
                </Stack>
                <input
                  ref={darkFaviconInputRef}
                  type="file"
                  accept=".svg,.png,.ico,.webp"
                  style={{ display: 'none' }}
                  onChange={(event) => uploadFavicon('dark', event.target.files?.[0])}
                />
              </Stack>
            </Stack>
          </Paper>
        </Stack>

        <Paper sx={{ p: 3 }}>
          <Stack spacing={2}>
            <Typography variant="h6" fontWeight={600}>
              Topbar Text Colors
            </Typography>
            <Grid container spacing={2}>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Light Theme Topbar Text Color"
                  type="color"
                  value={formState.lightTopbarTextColor}
                  onChange={handleTextChange('lightTopbarTextColor')}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                  helperText="Text color for topbar in light theme (default: white)"
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Dark Theme Topbar Text Color"
                  type="color"
                  value={formState.darkTopbarTextColor}
                  onChange={handleTextChange('darkTopbarTextColor')}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                  helperText="Text color for topbar in dark theme (default: white)"
                />
              </Grid>
            </Grid>
          </Stack>
        </Paper>

        <Paper sx={{ p: 3 }}>
          <Stack spacing={2}>
            <Typography variant="h6" fontWeight={600}>
              Light Theme
            </Typography>
            <Grid container spacing={2}>
              <Grid item xs={12} md={4}>
                <TextField
                  label="Primary Main"
                  type="color"
                  value={formState.light.primaryMain}
                  onChange={handleColorChange('light', 'primaryMain')}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                />
              </Grid>
              <Grid item xs={12} md={4}>
                <TextField
                  label="Secondary Main"
                  type="color"
                  value={formState.light.secondaryMain}
                  onChange={handleColorChange('light', 'secondaryMain')}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                />
              </Grid>
              <Grid item xs={12} md={4}>
                <TextField
                  label="Background Default"
                  type="color"
                  value={formState.light.backgroundDefault}
                  onChange={handleColorChange('light', 'backgroundDefault')}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                />
              </Grid>
            </Grid>
          </Stack>
        </Paper>

        <Paper sx={{ p: 3 }}>
          <Stack spacing={2}>
            <Typography variant="h6" fontWeight={600}>
              Dark Theme
            </Typography>
            <Grid container spacing={2}>
              <Grid item xs={12} md={4}>
                <TextField
                  label="Primary Main"
                  type="color"
                  value={formState.dark.primaryMain}
                  onChange={handleColorChange('dark', 'primaryMain')}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                />
              </Grid>
              <Grid item xs={12} md={4}>
                <TextField
                  label="Secondary Main"
                  type="color"
                  value={formState.dark.secondaryMain}
                  onChange={handleColorChange('dark', 'secondaryMain')}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                />
              </Grid>
              <Grid item xs={12} md={4}>
                <TextField
                  label="Background Default"
                  type="color"
                  value={formState.dark.backgroundDefault}
                  onChange={handleColorChange('dark', 'backgroundDefault')}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                />
              </Grid>
            </Grid>
          </Stack>
        </Paper>

        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} justifyContent="flex-end">
          <Button variant="outlined" color="inherit" onClick={handleResetBranding} disabled={saving}>
            Reset to Defaults
          </Button>
          <Button type="submit" variant="contained" disabled={saving}>
            {saving ? 'Saving…' : 'Save Changes'}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
};

export default BrandingSettings;
