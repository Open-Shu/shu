import React, { useMemo } from 'react';
import { Badge, Box, CircularProgress, IconButton, Tooltip, useTheme } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { Psychology as PsychologyIcon } from '@mui/icons-material';
import { keyframes } from '@emotion/react';

// Empty-state glow halo: a soft colored box-shadow that ripples outward
// from the button edge and fades. Each iteration restarts at 0px so the
// motion is always outward — never collapsing back inward. Color is
// driven by the theme so it tracks light/dark mode and brand changes.
const makeGlowPulseAnim = (color) => keyframes`
  0%   { box-shadow: 0 0 0 0 ${alpha(color, 0.55)}; }
  100% { box-shadow: 0 0 0 16px ${alpha(color, 0)}; }
`;

// Active-state vortex: applied to the brain glyph so files visibly get
// "pulled into" the user's memory during drag-over and upload.
const vortexAnim = keyframes`
  0% { transform: rotate(0deg) scale(1); }
  50% { transform: rotate(180deg) scale(1.08); }
  100% { transform: rotate(360deg) scale(1); }
`;

/**
 * BrainIcon — entry point for Personal Knowledge upload from the chat composer.
 *
 * State machine driven by props:
 *   - empty (no kb or 0 docs) and no errors  →  glow halo on button
 *   - dragActive or uploading                →  vortex on glyph + static halo
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
  const theme = useTheme();
  const accentColor = theme.palette.secondary.main;
  const glowPulseAnim = useMemo(() => makeGlowPulseAnim(accentColor), [accentColor]);

  const docCount = kb?.document_count || 0;
  const isEmpty = !kb || docCount === 0;
  const hasErrors = errorCount > 0;

  const tooltipText = hasErrors
    ? `${errorCount} upload${errorCount === 1 ? '' : 's'} need attention — click to retry`
    : isEmpty
      ? "Personal Knowledge — drop a file and I'll remember it"
      : `${docCount} doc${docCount === 1 ? '' : 's'} in Personal Knowledge`;

  const isAnimating = dragActive || uploading;

  // Empty-state glow runs only when nothing else is happening — vortex / drag /
  // error states take visual priority so the user isn't drowning in motion.
  const showGlowPulse = isEmpty && !hasErrors && !isAnimating;
  const buttonAnimation = showGlowPulse ? `${glowPulseAnim} 2s ease-out infinite` : 'none';
  const buttonBoxShadow = !showGlowPulse && dragActive ? `0 0 0 4px ${alpha(accentColor, 0.22)}` : undefined;

  const iconAnimation = isAnimating ? `${vortexAnim} 1.2s linear infinite` : 'none';
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
          borderColor: dragActive || showGlowPulse ? 'secondary.main' : 'divider',
          bgcolor: dragActive ? 'action.hover' : 'background.paper',
          width: { xs: 40, sm: 36 },
          height: { xs: 40, sm: 36 },
          borderRadius: '50%',
          flexShrink: 0,
          boxShadow: buttonBoxShadow,
          animation: buttonAnimation,
          transition: 'background-color 0.2s ease, border-color 0.2s ease',
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
                animation: iconAnimation,
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
