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
  Paper,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { userPreferencesAPI, extractDataFromResponse, formatError } from '../services/api';
import log from '../utils/log';
import NotImplemented from './NotImplemented';
import { useTheme } from '../contexts/ThemeContext';
import { buildUserPreferencesPayload } from '../utils/userPreferences';
import { FONT_FAMILIES, FONT_SIZE_SCALES, VALID_FONT_SIZE_SCALES, getFontStack } from '../utils/typography';

const INHERIT_VALUE = '__inherit__';

function useUserPreferences(themeMode) {
  const queryClient = useQueryClient();
  const [error, setError] = useState(null);
  const [preferences, setPreferences] = useState({
    memory_depth: 5,
    memory_similarity_threshold: 0.6,
    theme: themeMode,
    language: 'en',
    timezone: 'UTC',
    font_family: null,
    font_size_scale: null,
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

const TypographyPreview = ({ fontKey, scaleKey, brandHeadingFontKey }) => {
  const bodyStack = getFontStack(fontKey);
  const headingStack = getFontStack(brandHeadingFontKey);
  const multiplier = (FONT_SIZE_SCALES[scaleKey] ?? FONT_SIZE_SCALES.default).multiplier;
  // Anchor the preview to a fixed pixel size so the user sees the absolute
  // result regardless of the page-level root scaling. 16px reference = the
  // browser default before the 20% baseline reduction.
  const previewBase = 16 * multiplier;
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', mb: 1, fontSize: '12px' }}>
        Preview ({Math.round(previewBase * 100) / 100}px body)
      </Typography>
      <Box sx={{ fontFamily: headingStack, fontSize: `${previewBase * 1.5}px`, fontWeight: 600, mb: 0.5 }}>
        The quick brown fox
      </Box>
      <Box sx={{ fontFamily: bodyStack, fontSize: `${previewBase}px`, lineHeight: 1.5 }}>
        Jumps over the lazy dog. 1234567890 — sphinx of black quartz, judge my vow.
      </Box>
      <Box sx={{ fontFamily: bodyStack, fontSize: `${previewBase * 0.875}px`, color: 'text.secondary', mt: 0.5 }}>
        Secondary text uses the same body font at a smaller size.
      </Box>
      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', mt: 1.5, fontSize: '12px' }}>
        Reference (always 16px, unaffected by scale)
      </Typography>
      <Box sx={{ fontFamily: bodyStack, fontSize: '16px' }}>This line is always 16px.</Box>
    </Paper>
  );
};

export default function GeneralPreferencesSection() {
  const { themeMode, changeTheme, branding, resolvedFontFamily, changeFontFamily, changeFontScale } = useTheme();
  const { preferences, setPreferences, error, setError, mutation } = useUserPreferences(themeMode);

  const brandFontKey = branding?.brandFontFamily ?? null;
  const brandHeadingFontKey = branding?.brandHeadingFontFamily ?? brandFontKey ?? 'inter';

  const previewFontKey = preferences.font_family || resolvedFontFamily;
  const previewScaleKey = preferences.font_size_scale || 'default';

  // Match the theme-dropdown pattern: typography changes apply (and persist
  // via PATCH) immediately on selection so the live preview matches the page.
  const handleFontFamilyChange = (value) => {
    const normalized = value === INHERIT_VALUE ? null : value;
    setPreferences((prev) => ({ ...prev, font_family: normalized }));
    changeFontFamily(normalized);
  };

  const handleFontScaleChange = (_event, value) => {
    if (value === null) {
      return; // ToggleButtonGroup deselect — ignore
    }
    const normalized = value === INHERIT_VALUE ? null : value;
    setPreferences((prev) => ({ ...prev, font_size_scale: normalized }));
    changeFontScale(normalized);
  };

  const inheritLabel = brandFontKey
    ? `Default — Team Brand (${FONT_FAMILIES[brandFontKey]?.label ?? brandFontKey})`
    : 'Default (Inter)';

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

      <Typography variant="h6" gutterBottom sx={{ mt: 4, mb: 2 }}>
        Typography
      </Typography>

      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <Stack spacing={2}>
            <FormControl fullWidth>
              <InputLabel id="font-family-select-label">Font Family</InputLabel>
              <Select
                labelId="font-family-select-label"
                label="Font Family"
                value={preferences.font_family ?? INHERIT_VALUE}
                onChange={(e) => handleFontFamilyChange(e.target.value)}
              >
                <MenuItem value={INHERIT_VALUE}>{inheritLabel}</MenuItem>
                {Object.entries(FONT_FAMILIES).map(([key, def]) => (
                  <MenuItem key={key} value={key} sx={{ fontFamily: def.stack }}>
                    {def.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <Box>
              <Typography variant="body2" sx={{ mb: 1, color: 'text.secondary' }}>
                Font Size
              </Typography>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={preferences.font_size_scale ?? 'default'}
                onChange={handleFontScaleChange}
                aria-label="font size scale"
              >
                {VALID_FONT_SIZE_SCALES.map((key) => (
                  <ToggleButton key={key} value={key} aria-label={FONT_SIZE_SCALES[key].label}>
                    {FONT_SIZE_SCALES[key].label}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
              <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
                Default is the new baseline (20% smaller than the browser default).
              </Typography>
            </Box>
          </Stack>
        </Grid>
        <Grid item xs={12} md={6}>
          <TypographyPreview
            fontKey={previewFontKey}
            scaleKey={previewScaleKey}
            brandHeadingFontKey={brandHeadingFontKey}
          />
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
