import React, { useEffect, useMemo, useState } from 'react';
import { Box, Typography, useMediaQuery } from '@mui/material';
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

const ThinkingIndicator = React.memo(function ThinkingIndicator({ message }) {
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
      <Box
        role="status"
        aria-label={PLACEHOLDER_THINKING}
        sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 1 }}
      >
        <FeatherIcon aria-hidden sx={{ fontSize: 24, color: 'text.secondary' }} />
        <Typography aria-hidden variant="body2" sx={{ color: 'text.secondary' }}>
          {PLACEHOLDER_THINKING}
        </Typography>
      </Box>
    );
  }

  return (
    // role="status" + a stable aria-label gives assistive tech a
    // single, non-flickering "Thinking…" announcement instead of
    // hearing each rotating verb as the per-letter spans cycle.
    // Inner visuals (rotating word, drifting feather) are
    // aria-hidden — purely decorative.
    <Box
      role="status"
      aria-label={PLACEHOLDER_THINKING}
      sx={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        minHeight: 40,
        // overflow stays visible so the per-letter intro can bring
        // letters in from `translateX(-15px)` without the leftmost
        // letter getting clipped at the indicator's left edge — they
        // bleed harmlessly into the bubble's 16px padding before
        // reaching their settled position. The feather strip handles
        // its own clipping locally.
      }}
    >
      <Box aria-hidden sx={{ mr: 1.5, position: 'relative', flexShrink: 0 }}>
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
            // Per-letter inline-block spans lose italic kerning that a
            // single text run gets; some words render a few px wider
            // than the ghost and would otherwise wrap. `nowrap` keeps
            // them on one line — the slight extension past the ghost's
            // right edge spills into the 12px mr gap before the
            // feather, which is invisible margin.
            whiteSpace: 'nowrap',
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
        aria-hidden
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
