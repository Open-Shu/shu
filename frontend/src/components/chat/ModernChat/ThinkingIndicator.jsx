import React, { useEffect, useMemo, useState } from 'react';
import { Box, Typography, useMediaQuery } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { keyframes } from '@emotion/react';
import FeatherIcon from './FeatherIcon';
import { DEFAULT_POOL, PLUGIN_POOL, RAG_POOL, getPoolFor, pickNextWord } from './utils/thinkingPhrases';
import { PLACEHOLDER_THINKING } from './utils/chatConfig';

// Used as an invisible layout placeholder so the word area is always
// exactly as wide as the longest possible verb. Eliminates word-rotation
// resize without leaving excess dead space.
const LONGEST_WORD = [...DEFAULT_POOL, ...RAG_POOL, ...PLUGIN_POOL].reduce(
  (longest, word) => (word.length > longest.length ? word : longest),
  ''
);

const wordSx = {
  fontWeight: 500,
  fontStyle: 'italic',
};

const WORD_ROTATION_MS = 2200;
const WORD_TRANSITION_MS = 700;
const LETTER_DURATION_MS = 400;
const LETTER_STAGGER_MS = 25;
const FEATHER_WAFT_MS = 5000;
const BREATHING_MS = 4000;

// Wafting feather: slow horizontal swing with a 30° base rotation so
// the feather sits closer to horizontal than its natural diagonal.
// Two-point oscillation + ease-in-out gives a near-sinusoidal motion;
// no Y translate so the rotation arc through transform-origin: bottom
// center doesn't read as a vertical bob.
const featherWaft = keyframes`
  0%, 100% { transform: translateX(-6px) rotate(28deg); }
  50%      { transform: translateX(6px) rotate(32deg); }
`;

// Per-letter scatter: each letter animates independently with a
// stagger delay set via the `--letter-delay` CSS variable on its
// inline style. Leftmost letters move first so the transition reads
// as wind propagating left-to-right across the word.
const letterGustOut = keyframes`
  0%   { transform: translate(0, 0) rotate(0deg); opacity: 1; filter: blur(0); }
  100% { transform: translate(15px, -6px) rotate(12deg); opacity: 0; filter: blur(3px); }
`;

const letterGustIn = keyframes`
  0%   { transform: translate(-15px, 4px) rotate(-6deg); opacity: 0; filter: blur(3px); }
  100% { transform: translate(0, 0) rotate(0deg); opacity: 1; filter: blur(0); }
`;

const breathing = keyframes`
  0%, 100% { opacity: 0; }
  50%      { opacity: 0.04; }
`;

const ThinkingIndicator = React.memo(function ThinkingIndicator({ message }) {
  const theme = useTheme();
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const pool = useMemo(() => getPoolFor(message?.thinkingPool), [message?.thinkingPool]);
  const [currentWord, setCurrentWord] = useState(() => pickNextWord(pool, null));
  const [isLeaving, setIsLeaving] = useState(false);

  useEffect(() => {
    if (reduceMotion) {
      return undefined;
    }
    let pendingSwapId = null;
    const intervalId = setInterval(() => {
      setIsLeaving(true);
      pendingSwapId = setTimeout(() => {
        setCurrentWord((prev) => pickNextWord(pool, prev));
        setIsLeaving(false);
        pendingSwapId = null;
      }, WORD_TRANSITION_MS);
    }, WORD_ROTATION_MS);
    return () => {
      clearInterval(intervalId);
      if (pendingSwapId !== null) {
        clearTimeout(pendingSwapId);
      }
    };
  }, [pool, reduceMotion]);

  if (reduceMotion) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 1 }}>
        <FeatherIcon sx={{ fontSize: 24, color: 'text.secondary' }} />
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          {PLACEHOLDER_THINKING}
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        minHeight: 40,
        overflow: 'hidden',
      }}
    >
      <Box
        aria-hidden
        sx={{
          position: 'absolute',
          inset: 0,
          background: `radial-gradient(circle at 50% 50%, ${alpha(theme.palette.secondary.main, 1)} 0%, transparent 60%)`,
          animation: `${breathing} ${BREATHING_MS}ms ease-in-out infinite`,
          pointerEvents: 'none',
          willChange: 'opacity',
        }}
      />
      <Box sx={{ mr: 1.5, position: 'relative', flexShrink: 0 }}>
        <Typography variant="body2" sx={{ ...wordSx, visibility: 'hidden', display: 'inline-block' }}>
          {LONGEST_WORD}
        </Typography>
        <Typography
          key={`${currentWord}-${isLeaving ? 'out' : 'in'}`}
          variant="body2"
          sx={{
            ...wordSx,
            position: 'absolute',
            top: 0,
            left: 0,
            color: 'text.secondary',
            '& > span': {
              display: 'inline-block',
              // `both` fill-mode holds the 0% keyframe state during the
              // per-letter delay (rather than the un-animated DOM state),
              // so letters waiting their turn during the intro stay
              // invisible instead of flashing into view before their
              // animation kicks in.
              animation: isLeaving
                ? `${letterGustOut} ${LETTER_DURATION_MS}ms ease-out var(--letter-delay, 0ms) both`
                : `${letterGustIn} ${LETTER_DURATION_MS}ms ease-out var(--letter-delay, 0ms) both`,
              willChange: 'transform, opacity, filter',
            },
          }}
        >
          {currentWord.split('').map((letter, idx) => (
            // eslint-disable-next-line react/no-array-index-key
            <span key={idx} style={{ '--letter-delay': `${idx * LETTER_STAGGER_MS}ms` }}>
              {letter === ' ' ? ' ' : letter}
            </span>
          ))}
        </Typography>
      </Box>
      <Box
        sx={{
          position: 'relative',
          width: 64,
          height: 32,
          flexShrink: 0,
          overflow: 'hidden',
        }}
      >
        <FeatherIcon
          sx={{
            fontSize: 28,
            color: 'secondary.main',
            position: 'absolute',
            top: '50%',
            left: '50%',
            marginTop: '-14px',
            marginLeft: '-14px',
            transformOrigin: 'bottom center',
            animation: `${featherWaft} ${FEATHER_WAFT_MS}ms ease-in-out infinite`,
            willChange: 'transform',
          }}
        />
      </Box>
    </Box>
  );
});

export default ThinkingIndicator;
