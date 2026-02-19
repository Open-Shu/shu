/**
 * Password validation utilities and UI component.
 *
 * Validates passwords against the server-configured password policy
 * (fetched from /config/public). Provides both a pure validation function
 * and a React component that renders a live checklist.
 */

import React from 'react';

import Cancel from '@mui/icons-material/Cancel';
import CheckCircle from '@mui/icons-material/CheckCircle';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';

import configService from '../services/config';

/**
 * Validate a password against the server-configured policy.
 *
 * @param {string} password - The plaintext password to validate.
 * @returns {{ valid: boolean, checks: Array<{ label: string, met: boolean }> }}
 */
export function validatePassword(password) {
  const { policy, min_length: minLength, special_chars: specialChars } = configService.getPasswordPolicy();

  const checks = [
    {
      label: `At least ${minLength} characters`,
      met: password.length >= minLength,
    },
    {
      label: 'At least one uppercase letter',
      met: /[A-Z]/.test(password),
    },
    {
      label: 'At least one lowercase letter',
      met: /[a-z]/.test(password),
    },
    {
      label: 'At least one digit',
      met: /\d/.test(password),
    },
  ];

  if (policy === 'strict') {
    // Build a character-class regex from the server-provided special chars
    const escaped = specialChars.replace(/[-[\]{}()*+?.,\\^$|#\s]/g, '\\$&');
    checks.push({
      label: `At least one special character (${specialChars})`,
      met: new RegExp(`[${escaped}]`).test(password),
    });
  }

  return {
    valid: checks.every((c) => c.met),
    checks,
  };
}

/**
 * Real-time password requirements checklist.
 *
 * Renders a MUI List where each requirement shows a green checkmark or red X
 * depending on whether the current `password` prop satisfies it.
 *
 * @param {{ password: string }} props
 */
export function PasswordRequirements({ password = '' }) {
  const { checks } = validatePassword(password);

  return (
    <List dense disablePadding>
      {checks.map((check) => (
        <ListItem key={check.label} disableGutters sx={{ py: 0.25 }}>
          <ListItemIcon sx={{ minWidth: 28 }}>
            {check.met ? <CheckCircle fontSize="small" color="success" /> : <Cancel fontSize="small" color="error" />}
          </ListItemIcon>
          <ListItemText
            primary={check.label}
            primaryTypographyProps={{
              variant: 'body2',
              color: check.met ? 'text.primary' : 'text.secondary',
            }}
          />
        </ListItem>
      ))}
    </List>
  );
}
