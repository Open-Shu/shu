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
  logoUrl: '',
  faviconUrl: '',
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
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const [uploadingFavicon, setUploadingFavicon] = useState(false);

  const logoInputRef = useRef(null);
  const faviconInputRef = useRef(null);

  const resolvedLightTheme = useMemo(() => getThemeConfig('light', branding), [branding]);
  const resolvedDarkTheme = useMemo(() => getThemeConfig('dark', branding), [branding]);

  useEffect(() => {
    if (!brandingLoaded) {
      return;
    }

    setFormState({
      appName: branding.appName || '',
      logoUrl: branding.logoUrl || '',
      faviconUrl: branding.faviconUrl || '',
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
  }, [branding, brandingLoaded, resolvedDarkTheme, resolvedLightTheme]);

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
      logo_url: toNullable(formState.logoUrl),
      favicon_url: toNullable(formState.faviconUrl),
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
        logo_url: null,
        favicon_url: null,
        light_theme_overrides: null,
        dark_theme_overrides: null,
      });
      const data = extractDataFromResponse(response);
      setBranding(data);
      setStatus({ type: 'success', message: 'Branding reset to defaults.' });
    } catch (error) {
      const message = formatError(error);
      log.error('Branding reset failed', error);
      setStatus({ type: 'error', message });
    } finally {
      setSaving(false);
    }
  };

  const uploadAsset = async (type, file) => {
    if (!file) {
      return;
    }

    setStatus(null);
    if (type === 'logo') {
      setUploadingLogo(true);
    } else {
      setUploadingFavicon(true);
    }

    try {
      const response = type === 'logo' ? await brandingAPI.uploadLogo(file) : await brandingAPI.uploadFavicon(file);
      const data = extractDataFromResponse(response);
      setBranding(data);
      setStatus({
        type: 'success',
        message: `${type === 'logo' ? 'Logo' : 'Favicon'} updated.`,
      });
    } catch (error) {
      const message = formatError(error);
      log.error('Asset upload failed', error);
      setStatus({ type: 'error', message });
    } finally {
      if (type === 'logo' && logoInputRef.current) {
        logoInputRef.current.value = '';
      }
      if (type === 'favicon' && faviconInputRef.current) {
        faviconInputRef.current.value = '';
      }
      setUploadingLogo(false);
      setUploadingFavicon(false);
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
          description="Customize the look and feel of your application. Upload your logo, set a favicon, and configure color themes for both light and dark modes."
          icon={<PaletteIcon />}
          tips={[
            'Upload a logo image to replace the default branding throughout the app',
            'The favicon appears in browser tabs—upload a small square image',
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
                  Logo
                </Typography>
                <img
                  src={branding.logoUrl}
                  alt="Logo preview"
                  style={{ height: 80, objectFit: 'contain', maxWidth: '100%' }}
                />
                <Stack spacing={1}>
                  <Button
                    variant="outlined"
                    onClick={() => logoInputRef.current?.click()}
                    disabled={uploadingLogo}
                    fullWidth
                  >
                    {uploadingLogo ? 'Uploading…' : 'Upload Logo'}
                  </Button>
                  <input
                    value={formState.logoUrl}
                    onChange={handleTextChange('logoUrl')}
                    placeholder="https://example.com/logo.png"
                    type="hidden"
                  />
                </Stack>
                <input
                  ref={logoInputRef}
                  type="file"
                  accept=".png,.jpg,.jpeg,.svg,.webp"
                  style={{ display: 'none' }}
                  onChange={(event) => uploadAsset('logo', event.target.files?.[0])}
                />
              </Stack>
            </Stack>
          </Paper>
          <Paper sx={{ p: 3, flex: 1 }}>
            <Stack spacing={2}>
              <Stack spacing={1.5}>
                <Typography variant="subtitle1" fontWeight={600}>
                  Favicon
                </Typography>
                <img src={branding.faviconUrl} alt="Favicon preview" style={{ height: 80, objectFit: 'contain' }} />
                <Stack spacing={1}>
                  <Button
                    variant="outlined"
                    onClick={() => faviconInputRef.current?.click()}
                    disabled={uploadingFavicon}
                    fullWidth
                  >
                    {uploadingFavicon ? 'Uploading…' : 'Upload Favicon'}
                  </Button>
                  <input
                    value={formState.faviconUrl}
                    onChange={handleTextChange('faviconUrl')}
                    placeholder="https://example.com/favicon.png"
                    type="hidden"
                  />
                </Stack>
                <input
                  ref={faviconInputRef}
                  type="file"
                  accept=".svg,.png,.ico,.webp"
                  style={{ display: 'none' }}
                  onChange={(event) => uploadAsset('favicon', event.target.files?.[0])}
                />
              </Stack>
            </Stack>
          </Paper>
        </Stack>

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
