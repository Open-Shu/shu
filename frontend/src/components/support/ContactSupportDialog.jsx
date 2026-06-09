import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Stack,
  Typography,
  Divider,
  Tooltip,
  IconButton,
} from '@mui/material';
import {
  SupportAgent as SupportAgentIcon,
  Email as EmailIcon,
  ContentCopy as CopyIcon,
  Check as CheckIcon,
  AutoAwesome as AssistantIcon,
} from '@mui/icons-material';

import { SUPPORT_EMAIL } from '../../utils/constants';
import { SHU_ASSISTANT_ENABLED } from '../../config/featureFlags';
import log from '../../utils/log';

// How long the Copy button shows its "Copied" confirmation before resetting.
const COPIED_RESET_MS = 2000;

/**
 * ContactSupportDialog (SHU-857)
 *
 * Lightweight, pure-frontend support entry point opened from the profile menu.
 * Surfaces the support address with copy + mailto actions, and a distinct
 * "Shu Assistant" how-to-use-the-app stub that opens a new chat. This is NOT
 * a customer-support assistant — the copy keeps the two paths clearly separated.
 *
 * Props:
 *  - open: boolean
 *  - onClose: () => void
 *  - user?: { name, email, role } — used to prefill the mailto body
 *  - appName?: string — used in the mailto subject/body
 *  - version?: string — display version, shown inline and in the mailto body
 */
export default function ContactSupportDialog({ open, onClose, user, appName = 'Shu', version }) {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);

  // Prefill the mail body with the user's own account context (no privacy
  // concern — it's their data) so support can triage without a round-trip.
  // Kept short to stay well under mailto URL length limits (~2000 chars).
  const subject = `${appName} Support Request`;
  const body = [
    `App: ${appName}${version ? ` ${version}` : ''}`,
    `Name: ${user?.name || ''}`,
    `Email: ${user?.email || ''}`,
    `Role: ${user?.role || ''}`,
    '',
    'Describe your issue below:',
    '',
  ].join('\n');
  const mailtoHref = `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;

  const handleCopy = async () => {
    // Selectable address text below is the fallback when the Clipboard API is
    // unavailable (e.g. insecure HTTP context) — never throw at the user.
    if (!navigator.clipboard?.writeText) {
      log.warn('Clipboard API unavailable; address must be copied manually');
      return;
    }
    try {
      await navigator.clipboard.writeText(SUPPORT_EMAIL);
      setCopied(true);
      setTimeout(() => setCopied(false), COPIED_RESET_MS);
    } catch (err) {
      log.warn('Failed to copy support address to clipboard', err);
    }
  };

  const handleChatWithAssistant = () => {
    onClose();
    navigate('/chat');
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth aria-labelledby="contact-support-title">
      <DialogTitle id="contact-support-title" sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <SupportAgentIcon fontSize="small" />
        Contact Support
      </DialogTitle>

      <DialogContent dividers>
        <Stack spacing={1}>
          <Typography variant="subtitle2">Need to reach our team?</Typography>
          <Typography variant="body2" color="text.secondary">
            Email us and we&apos;ll get back to you as soon as we can.
          </Typography>

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="body1" sx={{ fontWeight: 600, userSelect: 'all', wordBreak: 'break-all' }}>
              {SUPPORT_EMAIL}
            </Typography>
            <Tooltip title={copied ? 'Copied' : 'Copy address'}>
              <IconButton size="small" onClick={handleCopy} aria-label="Copy support email address">
                {copied ? <CheckIcon fontSize="small" color="success" /> : <CopyIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
          </Box>

          <Box>
            <Button variant="contained" size="small" startIcon={<EmailIcon />} component="a" href={mailtoHref}>
              Email us
            </Button>
          </Box>

          {version && (
            <Typography variant="caption" color="text.disabled">
              {version}
            </Typography>
          )}
        </Stack>

        {SHU_ASSISTANT_ENABLED && (
          <>
            <Divider sx={{ my: 2 }} />

            <Stack spacing={1}>
              <Typography variant="subtitle2" sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                <AssistantIcon fontSize="small" />
                Have a question on how to use the app?
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Try chatting with Shu Assistant — a guide for getting around the app. For account, billing, or technical
                issues, email our team above instead.
              </Typography>
              <Box>
                <Button variant="outlined" size="small" startIcon={<AssistantIcon />} onClick={handleChatWithAssistant}>
                  Chat with Shu Assistant
                </Button>
              </Box>
            </Stack>
          </>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
