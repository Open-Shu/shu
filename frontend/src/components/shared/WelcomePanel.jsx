import React, { useMemo, useState } from 'react';
import { Box, Button, Chip, Fade, Skeleton, Stack, Tooltip, Typography, useMediaQuery } from '@mui/material';
import { keyframes } from '@emotion/react';
import { Add as AddIcon, Psychology as PersonalKbIcon } from '@mui/icons-material';

import FeatherIcon from '../chat/ModernChat/FeatherIcon';
import ModelConfigSelector from '../chat/ModernChat/ModelConfigSelector';
import { GREETINGS, SUBLINES, STARTER_CHIPS, getGreetingName, pickFresh } from '../chat/ModernChat/utils/welcomeCopy';

const FEATHER_FLOAT_MS = 6500;
const FADE_IN_MS = 450;
const CHIP_COUNT = 4;

// Idle "floating" feather for the welcome state — a gentle vertical drift with a
// slight rotation. Calmer/slower than ThinkingIndicator's featherWaft (which is a
// horizontal swing) so it reads as ambient branding rather than active work.
const featherFloat = keyframes`
  0%, 100% { transform: translateY(0) rotate(-5deg); }
  50%      { transform: translateY(-12px) rotate(5deg); }
`;

// Build the greeting line: named template when a first name resolved, anon
// fallback otherwise. Module-level so its branches don't inflate the
// component's cyclomatic complexity.
const buildGreeting = (template, name) => {
  if (!template) {
    return name ? `Welcome back, ${name}.` : 'Welcome back.';
  }
  return name ? template.named.replace('{name}', name) : template.anon;
};

// Personal KB call-out copy/colour by state (loading / present / absent).
const getKbAffordance = (personalKB, loading) => {
  if (loading) {
    return { label: 'Personal Knowledge…', color: 'default', tooltip: 'Loading your Personal Knowledge…' };
  }
  if (personalKB) {
    return {
      label: 'Personal Knowledge ready',
      color: 'secondary',
      tooltip: 'Your Personal Knowledge is attached to new chats.',
    };
  }
  return {
    label: 'Set up Personal Knowledge',
    color: 'default',
    tooltip: 'Attach documents with the brain icon in the composer to build your Personal Knowledge.',
  };
};

/**
 * Welcoming personality layer shared by the post-login landing screen
 * (`variant="landing"`) and the new-chat empty state (`variant="empty-chat"`).
 *
 * Landing renders a hero "New Chat" button that creates a conversation; the
 * empty state omits it (the composer is already present) and starter chips
 * prefill that composer directly. Both greet the user by a client-derived first
 * name, float the Shu feather, and call out model + Personal KB selection.
 *
 * Copy is picked once per mount (stable via lazy useState) from welcomeCopy.js
 * with cross-session de-dup. All motion honors prefers-reduced-motion.
 */
const WelcomePanel = React.memo(function WelcomePanel({
  variant = 'landing',
  user,
  appDisplayName,
  availableModelConfigs = [],
  selectedModelConfig,
  onModelChange,
  modelsLoading = false,
  personalKB = null,
  personalKBLoading = false,
  onSeedPrompt,
  onCreateConversation,
  createDisabled = false,
  canStartChat = true,
}) {
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const isLanding = variant === 'landing';

  // Name can resolve a beat after first paint (auth loads async). Recompute on
  // change so the greeting upgrades from anonymous to named without flicker.
  const name = useMemo(() => getGreetingName(user), [user]);

  // Pick copy once per mount. Lazy useState initializers run exactly once, so
  // the greeting/sub-line/chips stay stable across re-renders (unlike useMemo,
  // which React may discard) — important so the chips don't reshuffle when the
  // name resolves or the model list settles.
  const [greetingTpl] = useState(() => pickFresh(GREETINGS, 'greeting', { identify: (g) => g.named }));
  const [subline] = useState(() => pickFresh(SUBLINES, 'subline') || '');
  const [chips] = useState(
    () => pickFresh(STARTER_CHIPS, 'chips', { count: CHIP_COUNT, identify: (c) => c.label }) || []
  );

  const greeting = useMemo(() => buildGreeting(greetingTpl, name), [greetingTpl, name]);

  const hasModels = Array.isArray(availableModelConfigs) && availableModelConfigs.length > 0;
  const noModels = !modelsLoading && !hasModels;
  // On landing, a chip click creates a conversation, so it shares the hero's
  // gating (in-flight create / no resolved model). On the empty state it only
  // prefills the existing composer, so it's always available.
  const heroDisabled = createDisabled || !canStartChat;
  const seedDisabled = isLanding ? heroDisabled : false;
  const kb = getKbAffordance(personalKB, personalKBLoading);

  const handleChipClick = (prompt) => {
    if (!seedDisabled) {
      onSeedPrompt?.(prompt);
    }
  };

  return (
    // Landing owns its entrance fade. On the empty-chat variant the parent
    // (ModernChatView) wraps this in its own Fade for the hide-on-send exit, so
    // we skip the inner appear there to avoid a double fade-in.
    <Fade in appear={isLanding} timeout={reduceMotion ? 0 : FADE_IN_MS}>
      <Box
        role="region"
        aria-label="Welcome"
        sx={{
          width: '100%',
          maxWidth: 720,
          mx: 'auto',
          px: 2,
          textAlign: 'center',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 2,
        }}
      >
        {appDisplayName && (
          <Typography variant="overline" sx={{ color: 'text.secondary', letterSpacing: 2, lineHeight: 1 }}>
            {appDisplayName}
          </Typography>
        )}

        <FeatherIcon
          aria-hidden
          sx={{
            fontSize: 72,
            color: 'secondary.main',
            transformOrigin: 'center',
            ...(reduceMotion
              ? { transform: 'rotate(-5deg)' }
              : { animation: `${featherFloat} ${FEATHER_FLOAT_MS}ms ease-in-out infinite`, willChange: 'transform' }),
          }}
        />

        <Box>
          <Typography variant="h4" sx={{ fontWeight: 600, mb: 0.5 }}>
            {greeting}
          </Typography>
          {subline && (
            <Typography variant="body1" color="text.secondary" sx={{ maxWidth: 520, mx: 'auto' }}>
              {subline}
            </Typography>
          )}
        </Box>

        {modelsLoading ? (
          <Stack direction="row" spacing={1} justifyContent="center" sx={{ flexWrap: 'wrap', gap: 1 }}>
            {Array.from({ length: CHIP_COUNT }).map((_, i) => (
              // eslint-disable-next-line react/no-array-index-key
              <Skeleton key={`chip-skeleton-${i}`} variant="rounded" width={140} height={32} />
            ))}
          </Stack>
        ) : noModels ? (
          <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 460 }}>
            No models are configured yet — ask an admin to add one before starting a chat.
          </Typography>
        ) : (
          <>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', justifyContent: 'center', gap: 1 }}>
              {chips.map((chip) => (
                <Chip
                  key={chip.label}
                  label={chip.label}
                  variant="outlined"
                  clickable
                  disabled={seedDisabled}
                  onClick={() => handleChipClick(chip.prompt)}
                />
              ))}
            </Stack>

            {isLanding && (
              <Button
                variant="contained"
                color="secondary"
                size="large"
                startIcon={<AddIcon />}
                onClick={onCreateConversation}
                disabled={heroDisabled}
              >
                New Chat
              </Button>
            )}

            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={2}
              alignItems="center"
              justifyContent="center"
              sx={{ mt: 1, flexWrap: 'wrap', gap: 1 }}
            >
              <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0.5 }}>
                <ModelConfigSelector
                  availableModelConfigs={availableModelConfigs}
                  selectedModelConfig={selectedModelConfig}
                  onModelChange={onModelChange}
                  disabled={modelsLoading}
                />
                <Typography variant="caption" color="text.secondary">
                  Choose the model that answers you
                </Typography>
              </Box>

              <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0.5 }}>
                <Tooltip arrow title={kb.tooltip}>
                  <Chip icon={<PersonalKbIcon />} color={kb.color} variant="outlined" label={kb.label} />
                </Tooltip>
                <Typography variant="caption" color="text.secondary">
                  Ground answers in your own documents
                </Typography>
              </Box>
            </Stack>
          </>
        )}
      </Box>
    </Fade>
  );
});

export default WelcomePanel;
