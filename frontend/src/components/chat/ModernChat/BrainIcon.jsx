import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Box, CircularProgress, IconButton, Tooltip, useTheme } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { Psychology as PsychologyIcon } from '@mui/icons-material';
import { keyframes } from '@emotion/react';

// Empty-state glow halo: a soft colored box-shadow that ripples outward from the
// button edge and fades. Each iteration restarts at 0px so the motion is always
// outward. Color is theme-driven so it tracks light/dark mode and brand changes.
const makeGlowPulseAnim = (color) => keyframes`
  0%   { box-shadow: 0 0 0 0 ${alpha(color, 0.55)}; }
  100% { box-shadow: 0 0 0 16px ${alpha(color, 0)}; }
`;

// One-shot success ripple when a document finishes indexing (SHU-817 P3).
const makeSuccessPulseAnim = (color) => keyframes`
  0%   { box-shadow: 0 0 0 0 ${alpha(color, 0.5)}; }
  100% { box-shadow: 0 0 0 14px ${alpha(color, 0)}; }
`;

// Active-state vortex: applied to the brain glyph so files visibly get
// "pulled into" the user's memory during drag-over and upload.
const vortexAnim = keyframes`
  0% { transform: rotate(0deg) scale(1); }
  50% { transform: rotate(180deg) scale(1.08); }
  100% { transform: rotate(360deg) scale(1); }
`;

// Count-pop: the doc-count badge briefly scales up when a new doc lands (P3).
const countPopAnim = keyframes`
  0%   { transform: scale(1); }
  40%  { transform: scale(1.4); }
  100% { transform: scale(1); }
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
 *
 * Plus (P3): the badge count-pops when a doc is added, and the button emits a
 * one-shot success ripple when indexing finishes.
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

  // S5: the glow reads the admin-configurable secondary brand color and feeds it
  // to alpha(), which THROWS on an unparseable color and would crash the composer.
  // Fall back to the (always-valid) primary color if secondary can't be parsed.
  const safeAccent = useMemo(() => {
    const candidate = theme.palette.secondary.main;
    try {
      alpha(candidate, 0.5);
      return candidate;
    } catch {
      return theme.palette.primary.main;
    }
  }, [theme.palette.secondary.main, theme.palette.primary.main]);

  const glowPulseAnim = useMemo(() => makeGlowPulseAnim(safeAccent), [safeAccent]);
  const successPulseAnim = useMemo(() => makeSuccessPulseAnim(safeAccent), [safeAccent]);

  const docCount = kb?.document_count || 0;
  const isEmpty = !kb || docCount === 0;
  const hasErrors = errorCount > 0;

  // P3 transitions: detect a count increase (pop) and indexing finishing (pulse).
  const prevCountRef = useRef(docCount);
  const prevIndexingRef = useRef(indexing);
  const [pop, setPop] = useState(false);
  const [successPulse, setSuccessPulse] = useState(false);

  useEffect(() => {
    const grew = docCount > prevCountRef.current;
    const finishedIndexing = prevIndexingRef.current && !indexing;
    prevCountRef.current = docCount;
    prevIndexingRef.current = indexing;
    if (grew) {
      setPop(true);
    }
    if (finishedIndexing && docCount > 0) {
      setSuccessPulse(true);
    }
  }, [docCount, indexing]);

  useEffect(() => {
    if (!pop) {
      return undefined;
    }
    const t = setTimeout(() => setPop(false), 450);
    return () => clearTimeout(t);
  }, [pop]);

  const tooltipText = hasErrors
    ? `${errorCount} file${errorCount === 1 ? '' : 's'} couldn't be added — click to review`
    : isEmpty
      ? 'Your Personal Knowledge — add files, then ask me about them'
      : `${docCount} doc${docCount === 1 ? '' : 's'} ready — ask me anything about them`;

  const isAnimating = dragActive || uploading;

  // Empty-state glow runs only when nothing else is happening — vortex / drag /
  // error states take visual priority so the user isn't drowning in motion.
  const showGlowPulse = isEmpty && !hasErrors && !isAnimating;

  let buttonAnimation = 'none';
  if (showGlowPulse) {
    buttonAnimation = `${glowPulseAnim} 2s ease-out infinite`;
  } else if (successPulse) {
    buttonAnimation = `${successPulseAnim} 0.8s ease-out`;
  }
  const buttonBoxShadow = !showGlowPulse && dragActive ? `0 0 0 4px ${alpha(safeAccent, 0.22)}` : undefined;

  const iconAnimation = isAnimating ? `${vortexAnim} 1.2s linear infinite` : 'none';
  const iconColor = hasErrors ? 'error.main' : isEmpty ? 'text.secondary' : 'primary.main';

  const badgeContent = hasErrors ? '!' : docCount > 0 ? docCount : null;
  const badgeColor = hasErrors ? 'error' : 'primary';

  return (
    <Tooltip title={tooltipText}>
      <IconButton
        onClick={onClick}
        size="medium"
        // Only the finite success pulse fires animationend (the infinite glow loops
        // via animationiteration), so this safely clears the one-shot pulse.
        onAnimationEnd={successPulse ? () => setSuccessPulse(false) : undefined}
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
        <Badge
          badgeContent={badgeContent}
          color={badgeColor}
          overlap="circular"
          invisible={badgeContent === null}
          sx={{ '& .MuiBadge-badge': { animation: pop ? `${countPopAnim} 0.4s ease-out` : 'none' } }}
        >
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
