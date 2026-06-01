import React, { useRef, useState } from 'react';
import {
  Avatar,
  Box,
  Button,
  ButtonBase,
  Paper,
  Radio,
  RadioGroup,
  FormControlLabel,
  Stack,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';

import { brandingAPI, extractDataFromResponse, formatError } from '../../../services/api';
import { resolveBranding } from '../../../utils/brandingUtils';
import { CURATED_AVATARS, resolveCuratedAvatar } from '../../chat/ModernChat/avatars';
import log from '../../../utils/log';

const PREVIEW_DESKTOP_SIZE = 36;
const PREVIEW_MOBILE_SIZE = 20;

const AssistantAvatarSection = ({ branding, setBranding, setStatus }) => {
  const theme = useTheme();
  const uploadInputRef = useRef(null);
  const [uploading, setUploading] = useState(false);

  const resolved = resolveBranding(branding);
  const mode = resolved.assistantAvatarMode || 'curated';
  const curatedId = resolved.assistantAvatarCuratedId || 'shu_feather';
  const assetUrl = resolved.assistantAvatarAssetUrl || null;

  const persistPatch = async (payload, successMessage) => {
    setStatus?.(null);
    try {
      const response = await brandingAPI.updateBranding(payload);
      const data = extractDataFromResponse(response);
      setBranding(data);
      if (successMessage) {
        setStatus?.({ type: 'success', message: successMessage });
      }
    } catch (error) {
      log.error('Avatar update failed', error);
      setStatus?.({ type: 'error', message: formatError(error) });
    }
  };

  const handleModeChange = (event) => {
    const next = event.target.value;
    if (next === 'custom' && !assetUrl) {
      // Defer the mode flip until an asset is actually uploaded. The upload
      // endpoint sets mode='custom' atomically; switching first would leave
      // the chat without an avatar to render.
      uploadInputRef.current?.click();
      return;
    }
    if (next === mode) {
      return;
    }
    persistPatch({ assistant_avatar_mode: next }, 'Avatar mode updated.');
  };

  const handleCuratedSelect = (id) => {
    if (id === curatedId && mode === 'curated') {
      return;
    }
    persistPatch({ assistant_avatar_mode: 'curated', assistant_avatar_curated_id: id }, 'Avatar icon updated.');
  };

  const handleUploadClick = () => {
    uploadInputRef.current?.click();
  };

  const handleUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    setStatus?.(null);
    setUploading(true);
    try {
      const response = await brandingAPI.uploadAssistantAvatar(file);
      const data = extractDataFromResponse(response);
      setBranding(data);
      setStatus?.({ type: 'success', message: 'Assistant avatar uploaded.' });
    } catch (error) {
      log.error('Assistant avatar upload failed', error);
      setStatus?.({ type: 'error', message: formatError(error) });
    } finally {
      setUploading(false);
      if (uploadInputRef.current) {
        uploadInputRef.current.value = '';
      }
    }
  };

  const renderPreviewAvatar = (size) => {
    if (mode === 'none') {
      return (
        <Box
          sx={{
            width: size,
            height: size,
            border: `1px dashed ${theme.palette.divider}`,
            borderRadius: '50%',
          }}
        />
      );
    }
    if (mode === 'custom' && assetUrl) {
      return <Avatar src={assetUrl} alt={resolved.appName || 'Assistant'} sx={{ width: size, height: size }} />;
    }
    const entry = resolveCuratedAvatar(curatedId);
    const IconComponent = entry.component;
    return (
      <Avatar
        sx={{
          width: size,
          height: size,
          bgcolor: theme.palette.secondary.main,
          color: theme.palette.secondary.contrastText,
        }}
      >
        <IconComponent sx={{ fontSize: size * 0.6 }} />
      </Avatar>
    );
  };

  return (
    <Paper sx={{ p: 3 }}>
      <Stack spacing={2}>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={2}
          alignItems={{ xs: 'flex-start', sm: 'center' }}
          justifyContent="space-between"
        >
          <Box>
            <Typography variant="h6" fontWeight={600}>
              Assistant Avatar
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Choose the icon shown next to AI responses in chat. Changes apply on the next chat refresh.
            </Typography>
          </Box>
          <Stack direction="row" spacing={2} alignItems="center">
            <Stack alignItems="center" spacing={0.5}>
              {renderPreviewAvatar(PREVIEW_DESKTOP_SIZE)}
              <Typography variant="caption" color="text.secondary">
                Desktop
              </Typography>
            </Stack>
            <Stack alignItems="center" spacing={0.5}>
              {renderPreviewAvatar(PREVIEW_MOBILE_SIZE)}
              <Typography variant="caption" color="text.secondary">
                Mobile
              </Typography>
            </Stack>
          </Stack>
        </Stack>

        <RadioGroup row value={mode} onChange={handleModeChange}>
          <FormControlLabel value="curated" control={<Radio />} label="Curated icon" />
          <FormControlLabel value="custom" control={<Radio />} label="Custom upload" />
          <FormControlLabel value="none" control={<Radio />} label="No icons (clean conversation view)" />
        </RadioGroup>

        {mode === 'curated' && (
          <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
            {CURATED_AVATARS.map((entry) => {
              const Icon = entry.component;
              const selected = entry.id === curatedId;
              return (
                <ButtonBase
                  key={entry.id}
                  aria-label={entry.label}
                  aria-pressed={selected}
                  onClick={() => handleCuratedSelect(entry.id)}
                  sx={{
                    p: 1,
                    borderRadius: 1,
                    border: `2px solid ${selected ? theme.palette.primary.main : theme.palette.divider}`,
                    bgcolor: selected ? theme.palette.action.selected : 'transparent',
                  }}
                >
                  <Avatar
                    sx={{
                      width: 40,
                      height: 40,
                      bgcolor: theme.palette.secondary.main,
                      color: theme.palette.secondary.contrastText,
                    }}
                  >
                    <Icon />
                  </Avatar>
                </ButtonBase>
              );
            })}
          </Stack>
        )}

        {mode === 'custom' && (
          <Stack spacing={1}>
            <Button
              variant="outlined"
              onClick={handleUploadClick}
              disabled={uploading}
              sx={{ alignSelf: 'flex-start' }}
            >
              {uploading ? 'Uploading…' : assetUrl ? 'Replace Image' : 'Upload Image'}
            </Button>
            <Typography variant="caption" color="text.secondary">
              PNG, JPG, or WebP. Recommended: square image, at least 128×128px.
            </Typography>
          </Stack>
        )}

        {mode === 'none' && (
          <Typography variant="body2" color="text.secondary">
            Both the assistant and user avatars are hidden in chat. Messages still distinguish speakers via alignment
            and bubble color.
          </Typography>
        )}

        <input
          ref={uploadInputRef}
          type="file"
          accept=".png,.jpg,.jpeg,.webp"
          style={{ display: 'none' }}
          onChange={handleUpload}
        />
      </Stack>
    </Paper>
  );
};

export default AssistantAvatarSection;
