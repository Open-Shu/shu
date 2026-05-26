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
const WORD_TRANSITION_MS = 600;
const FEATHER_WAFT_MS = 5000;
const BREATHING_MS = 4000;

// Wafting feather: predominantly horizontal swing with mild rotation
// around the quill base (transform-origin: bottom center on the icon).
// Asymmetric keyframe placement + tiny Y variance prevents the motion
// from reading as a rhythmic up/down bob.
const featherWaft = keyframes`
  0%, 100% { transform: translate(-10px, 0) rotate(-6deg); }
  30%      { transform: translate(-3px, 1px) rotate(0deg); }
  50%      { transform: translate(10px, 0) rotate(6deg); }
  75%      { transform: translate(2px, -1px) rotate(-1deg); }
`;

const wordGustOut = keyframes`
  0%   { transform: translateX(0) skewX(0deg); opacity: 1; filter: blur(0); }
  100% { transform: translateX(20px) skewX(-8deg); opacity: 0; filter: blur(4px); }
`;

const wordGustIn = keyframes`
  0%   { transform: translateX(-20px) skewX(8deg); opacity: 0; filter: blur(4px); }
  100% { transform: translateX(0) skewX(0deg); opacity: 1; filter: blur(0); }
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
            animation: isLeaving
              ? `${wordGustOut} ${WORD_TRANSITION_MS}ms ease-out forwards`
              : `${wordGustIn} ${WORD_TRANSITION_MS}ms ease-out forwards`,
            willChange: 'transform, opacity, filter',
          }}
        >
          {currentWord}
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
