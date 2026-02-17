import React from 'react';
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
  Stack,
  Chip,
  alpha,
  useTheme,
} from '@mui/material';
import { Warning as WarningIcon, Security as SecurityIcon } from '@mui/icons-material';

const GUIDE_STEPS = [
  {
    text: (label) => (
      <>
        Click <strong>"Authorize Now"</strong> to open the {label} sign-in window, then choose your account.
      </>
    ),
    hasButton: true,
    img: '/1.png',
    alt: 'Choose a Google account',
  },
  {
    text: (label) => (
      <>
        You'll see a warning saying <em>"{label} hasn't verified this app"</em>. Click <strong>"Advanced"</strong> at
        the bottom left of that screen.
      </>
    ),
    img: '/2.png',
    alt: 'Click Advanced on the warning screen',
  },
  {
    text: () => (
      <>
        Click <strong>"Go to Shu (unsafe)"</strong> to proceed.
      </>
    ),
    img: '/3.png',
    alt: 'Click Go to Shu (unsafe)',
  },
  {
    text: () => (
      <>
        Click <strong>"Select all"</strong> to grant the requested permissions, then click <strong>"Continue"</strong>{' '}
        to complete authorization.
      </>
    ),
    img: '/4.png',
    alt: 'Select all permissions and click Continue',
  },
];

function GuideSteps({ providerLabel, imgBorder, authorizing, onAuthorize }) {
  return (
    <Stack spacing={3}>
      {GUIDE_STEPS.map((step, i) => (
        <Box key={step.img}>
          <Typography variant="body2" sx={{ mb: 1 }}>
            <strong>Step {i + 1}:</strong> {step.text(providerLabel)}
          </Typography>
          {step.hasButton && (
            <Button
              variant="contained"
              size="large"
              onClick={onAuthorize}
              disabled={authorizing}
              sx={{ mb: 2 }}
              fullWidth
            >
              {authorizing ? <CircularProgress size={20} color="inherit" /> : 'Authorize Now'}
            </Button>
          )}
          <Box
            component="img"
            src={step.img}
            alt={step.alt}
            sx={{ width: '100%', borderRadius: 1, border: imgBorder }}
          />
        </Box>
      ))}
    </Stack>
  );
}

/**
 * OAuthGuideDialog
 *
 * Shown before opening the Google OAuth consent screen to guide users
 * through the "Google hasn't verified this app" warning that appears
 * while the app is pending verification.
 */
export default function OAuthGuideDialog({ open, onClose, provider, scopes = [], authorizing = false, onAuthorize }) {
  const theme = useTheme();
  const providerLabel = provider ? provider.charAt(0).toUpperCase() + provider.slice(1) : 'Provider';
  const subtleBorder = `1px solid ${alpha(theme.palette.divider, theme.palette.action.disabledOpacity)}`;

  return (
    <Dialog open={open} onClose={authorizing ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <SecurityIcon color="primary" />
        Before You Authorize
      </DialogTitle>
      <DialogContent dividers>
        <Stack spacing={3}>
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Why am I seeing a warning?
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Our app is currently pending {providerLabel}'s verification review. During this period, {providerLabel}{' '}
              displays an <strong>"app not verified"</strong> warning. This is expected and safe to proceed through —
              the app simply hasn't completed the review process yet.
            </Typography>
          </Box>

          <Box>
            <Typography variant="subtitle2" gutterBottom>
              What are these permissions for?
            </Typography>
            <Typography variant="body2" color="text.secondary">
              The permissions you're granting allow your subscribed plugins to access your {providerLabel} data on your
              behalf — for example, reading emails or files so they can be processed by Shu's experience system.
            </Typography>
            {scopes.length > 0 && (
              <Box sx={{ mt: 1, display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                {scopes.map((scope) => (
                  <Chip key={scope} label={scope.split('/').pop()} size="small" variant="outlined" />
                ))}
              </Box>
            )}
          </Box>

          <Box
            sx={{
              p: 2,
              borderRadius: 1,
              backgroundColor: alpha(theme.palette.warning.main, theme.palette.action.hoverOpacity),
              border: `1px solid ${alpha(theme.palette.warning.main, theme.palette.action.disabledOpacity)}`,
            }}
          >
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
              <WarningIcon fontSize="small" color="warning" />
              <Typography variant="subtitle2">Step-by-step guide</Typography>
            </Stack>
            <GuideSteps
              providerLabel={providerLabel}
              imgBorder={subtleBorder}
              authorizing={authorizing}
              onAuthorize={onAuthorize}
            />
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={authorizing}>
          Cancel
        </Button>
        <Button variant="contained" onClick={onAuthorize} disabled={authorizing}>
          {authorizing ? <CircularProgress size={18} color="inherit" /> : 'Authorize Now'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
