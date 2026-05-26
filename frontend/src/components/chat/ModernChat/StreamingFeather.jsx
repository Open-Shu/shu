import React, { useEffect, useRef, useState } from 'react';
import { Box, useMediaQuery } from '@mui/material';
import { keyframes } from '@emotion/react';
import FeatherIcon from './FeatherIcon';

const WRITING_CYCLE_MS = 4000;
const WRITING_BOB_MS = 240;
const SETTLE_MS = 1000;
const FEATHER_SIZE = 28;
const STRIP_HEIGHT = 36;

// Carriage: writes left→right over 80% of the cycle, returns quickly
// (and smoothly) over the remaining 20%. Travel distance is derived
// from the strip's own width (100cqw) minus the feather width, so it
// adapts to whatever bubble it lands in.
const writingCarriage = keyframes`
  0%   { transform: translateX(0); animation-timing-function: ease-in-out; }
  80%  { transform: translateX(calc(100cqw - ${FEATHER_SIZE}px)); animation-timing-function: ease-in-out; }
  100% { transform: translateX(0); }
`;

// Bob: nib hitting paper as the carriage travels.
const writingBob = keyframes`
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-3px); }
`;

// Settle: an invisible hand lifts the quill briefly, then lays it
// flat on the page. The 25% keyframe is the tiny lift-and-tilt before
// the drop; the 100% endpoint is near-horizontal (-75°) for the
// laid-to-rest pose.
const settleDown = keyframes`
  0%   { transform: translateY(0) rotate(0deg); opacity: 1; }
  25%  { transform: translateY(-2px) rotate(15deg); }
  100% { transform: translateY(18px) rotate(75deg); opacity: 0; }
`;

const PHASES = { STREAMING: 'streaming', SETTLING: 'settling', GONE: 'gone' };

const StreamingFeather = React.memo(function StreamingFeather({ isStreaming }) {
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const [phase, setPhase] = useState(() => (isStreaming ? PHASES.STREAMING : PHASES.GONE));
  // Timer ref lives outside the effect so that a deps-change effect
  // re-run doesn't cancel an in-flight settle. The effect returning a
  // cleanup would fire `clearTimeout` on the very next render (phase
  // changes STREAMING→SETTLING) and stall the state machine before
  // SETTLE_MS ever elapses, leaving the strip mounted forever.
  const settleTimeoutRef = useRef(null);

  useEffect(() => {
    if (isStreaming && phase !== PHASES.STREAMING) {
      if (settleTimeoutRef.current !== null) {
        clearTimeout(settleTimeoutRef.current);
        settleTimeoutRef.current = null;
      }
      setPhase(PHASES.STREAMING);
      return;
    }
    if (!isStreaming && phase === PHASES.STREAMING) {
      if (reduceMotion) {
        setPhase(PHASES.GONE);
        return;
      }
      setPhase(PHASES.SETTLING);
      settleTimeoutRef.current = setTimeout(() => {
        settleTimeoutRef.current = null;
        setPhase(PHASES.GONE);
      }, SETTLE_MS);
    }
  }, [isStreaming, phase, reduceMotion]);

  useEffect(
    () => () => {
      if (settleTimeoutRef.current !== null) {
        clearTimeout(settleTimeoutRef.current);
        settleTimeoutRef.current = null;
      }
    },
    []
  );

  if (phase === PHASES.GONE) {
    return null;
  }

  if (reduceMotion) {
    return (
      <Box sx={{ width: '100%', display: 'flex', alignItems: 'center', py: 0.5 }}>
        <FeatherIcon sx={{ fontSize: FEATHER_SIZE, color: 'secondary.main' }} />
      </Box>
    );
  }

  const isSettling = phase === PHASES.SETTLING;

  return (
    <Box
      sx={{
        width: '100%',
        height: STRIP_HEIGHT,
        position: 'relative',
        overflow: 'hidden',
        containerType: 'inline-size',
      }}
    >
      <Box
        sx={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: FEATHER_SIZE,
          height: STRIP_HEIGHT,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          animation: `${writingCarriage} ${WRITING_CYCLE_MS}ms infinite`,
          // Pausing the carriage during settle keeps the feather
          // wherever it was writing — settle drifts down from there
          // rather than snapping back to the left edge.
          animationPlayState: isSettling ? 'paused' : 'running',
          willChange: 'transform',
        }}
      >
        <FeatherIcon
          sx={{
            fontSize: FEATHER_SIZE,
            color: 'secondary.main',
            display: 'block',
            transformOrigin: 'bottom center',
            animation: isSettling
              ? `${settleDown} ${SETTLE_MS}ms ease-out forwards`
              : `${writingBob} ${WRITING_BOB_MS}ms ease-in-out infinite`,
            willChange: 'transform, opacity',
          }}
        />
      </Box>
    </Box>
  );
});

export default StreamingFeather;
