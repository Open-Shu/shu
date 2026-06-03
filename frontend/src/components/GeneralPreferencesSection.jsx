import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
  Alert,
  Box,
  Button,
  Grid,
  FormControl,
  FormControlLabel,
  InputLabel,
  Select,
  MenuItem,
  Paper,
  Stack,
  Switch,
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

const TypographyPreview = ({ fontKey, brandHeadingFontKey }) => {
  const bodyStack = getFontStack(fontKey);
  const headingStack = getFontStack(brandHeadingFontKey);
  // Sizes use rem so the preview reflects the user's current scale — the
  // body line below is rendered at the exact size chat messages will use.
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Box
        sx={{
          color: 'text.secondary',
          fontSize: '0.75rem',
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
          mb: 1.5,
          pb: 1,
          borderBottom: '1px dashed',
          borderColor: 'divider',
        }}
      >
        Preview
      </Box>
      <Box sx={{ fontFamily: headingStack, fontSize: '1.5rem', fontWeight: 600, mb: 0.5 }}>Page & section headings</Box>
      <Box sx={{ fontFamily: bodyStack, fontSize: '1rem', lineHeight: 1.5 }}>
        Chat messages and body text. Sphinx of black quartz, judge my vow. 1234567890.
      </Box>
      <Box sx={{ fontFamily: bodyStack, fontSize: '0.875rem', color: 'text.secondary', mt: 0.5 }}>
        Captions, hints, and supporting text.
      </Box>
      <Box
        sx={{
          fontFamily: bodyStack,
          fontSize: '16px',
          color: 'text.secondary',
          mt: 1.5,
          pt: 1,
          borderTop: '1px dashed',
          borderColor: 'divider',
        }}
      >
        Fixed reference, unaffected by your selection.
      </Box>
    </Paper>
  );
};

export default function GeneralPreferencesSection() {
  const {
    themeMode,
    changeTheme,
    branding,
    resolvedFontFamily,
    resolvedHeadingFontFamily,
    changeFontFamily,
    changeFontScale,
  } = useTheme();
  const { preferences, setPreferences, error, setError, mutation } = useUserPreferences(themeMode);
  const queryClient = useQueryClient();

  const brandFontKey = branding?.brandFontFamily ?? null;

  const previewFontKey = preferences.font_family || resolvedFontFamily;

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
    // Reset semantics: picking "Default" persists null (= inherit / use the
    // shipped baseline) rather than locking the user to the literal label.
    // Future re-tuning of the scale multipliers then applies to null-holders
    // automatically without a data migration. Mirrors the font_family
    // INHERIT_VALUE pattern.
    const normalized = value === INHERIT_VALUE || value === 'default' ? null : value;
    setPreferences((prev) => ({ ...prev, font_size_scale: normalized }));
    changeFontScale(normalized);
  };

  // Persist immediately via PATCH (mirroring the brain-popover toggle) rather than
  // riding the full-PUT "Save Settings" path, which sends the whole coerced payload
  // and could clobber other prefs from a partially-loaded / cross-device state
  // (SHU-817 S4 — PATCH-so-other-prefs-aren't-clobbered). Optimistic with rollback.
  const handleAutoAttachChange = (event) => {
    const enabled = event.target.checked;
    setPreferences((prev) => ({ ...prev, auto_attach_personal_kb: enabled }));
    userPreferencesAPI
      .patchPreferences({ auto_attach_personal_kb: enabled })
      // Refresh the shared 'user-preferences' cache so the chat — which seeds its
      // own copy from this same query — picks up the new value on its next read
      // instead of briefly applying the stale one. Mirrors the popover path (M4).
      .then(() => queryClient.invalidateQueries('user-preferences'))
      .catch((err) => {
        log.warn('Failed to update auto-attach preference:', formatError(err).message);
        // !enabled is the pre-click toggle state (the switch was at !enabled before
        // this change), i.e. the correct value to restore for a single failed toggle.
        setPreferences((prev) => ({ ...prev, auto_attach_personal_kb: !enabled }));
        setError("Couldn't save your auto-attach preference. Please try again.");
      });
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
        Chat
      </Typography>
      <FormControlLabel
        control={
          <Switch
            checked={preferences.auto_attach_personal_kb !== false}
            onChange={handleAutoAttachChange}
            inputProps={{ 'aria-label': 'Auto-attach Personal Knowledge to new chats' }}
          />
        }
        label="Auto-attach my Personal Knowledge to new chats"
      />
      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
        When on, your Personal Knowledge is searched in every new conversation. Turn it off to attach it manually per
        chat.
      </Typography>

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
              <Box sx={{ overflowX: 'auto', maxWidth: '100%' }}>
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
              </Box>
              <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
                Applies to all text across the app.
              </Typography>
            </Box>
          </Stack>
        </Grid>
        <Grid item xs={12} md={6}>
          <TypographyPreview fontKey={previewFontKey} brandHeadingFontKey={resolvedHeadingFontFamily} />
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
