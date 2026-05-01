import React from 'react';
import { Badge, Box, CircularProgress, IconButton, Tooltip } from '@mui/material';
import { Psychology as PsychologyIcon } from '@mui/icons-material';
import { keyframes } from '@emotion/react';

const pulseAnim = keyframes`
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.1); opacity: 0.65; }
`;

const vortexAnim = keyframes`
  0% { transform: rotate(0deg) scale(1); }
  50% { transform: rotate(180deg) scale(1.08); }
  100% { transform: rotate(360deg) scale(1); }
`;

/**
 * BrainIcon — entry point for Personal Knowledge upload from the chat composer.
 *
 * State machine driven by props:
 *   - empty (no kb or 0 docs) and no errors  →  pulse
 *   - dragActive or uploading                →  vortex (with halo)
 *   - indexing                               →  spinner glyph
 *   - has docs, no errors                    →  solid + count badge
 *   - errors > 0                             →  solid + red `!` badge (non-clearing)
 */
const BrainIcon = React.memo(function BrainIcon({
  kb,
  uploading = false,
  indexing = false,
  errorCount = 0,
  dragActive = false,
  onClick,
}) {
  const docCount = kb?.document_count || 0;
  const isEmpty = !kb || docCount === 0;
  const hasErrors = errorCount > 0;

  const tooltipText = hasErrors
    ? `${errorCount} upload${errorCount === 1 ? '' : 's'} need attention — click to retry`
    : isEmpty
      ? "Personal Knowledge — drop a file and I'll remember it"
      : `${docCount} doc${docCount === 1 ? '' : 's'} in Personal Knowledge`;

  const isAnimating = dragActive || uploading;
  const animation = isAnimating
    ? `${vortexAnim} 1.2s linear infinite`
    : isEmpty && !hasErrors
      ? `${pulseAnim} 2s ease-in-out infinite`
      : 'none';

  const iconColor = hasErrors ? 'error.main' : isEmpty ? 'text.secondary' : 'primary.main';

  const badgeContent = hasErrors ? '!' : docCount > 0 ? docCount : null;
  const badgeColor = hasErrors ? 'error' : 'primary';

  return (
    <Tooltip title={tooltipText}>
      <IconButton
        onClick={onClick}
        size="medium"
        sx={{
          border: 1,
          borderColor: dragActive ? 'primary.main' : 'divider',
          bgcolor: dragActive ? 'action.hover' : 'background.paper',
          width: { xs: 40, sm: 36 },
          height: { xs: 40, sm: 36 },
          borderRadius: '50%',
          flexShrink: 0,
          boxShadow: dragActive ? '0 0 0 4px rgba(25, 118, 210, 0.18)' : 'none',
          transition: 'box-shadow 0.2s ease, background-color 0.2s ease, border-color 0.2s ease',
        }}
        aria-label="Personal Knowledge"
      >
        <Badge badgeContent={badgeContent} color={badgeColor} overlap="circular" invisible={badgeContent === null}>
          {indexing && !isAnimating ? (
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24 }}>
              <CircularProgress size={20} thickness={5} />
            </Box>
          ) : (
            <PsychologyIcon
              sx={{
                color: iconColor,
                animation,
                transformOrigin: 'center',
              }}
            />
          )}
        </Badge>
      </IconButton>
    </Tooltip>
  );
});

export default BrainIcon;
