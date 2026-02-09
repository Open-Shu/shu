import React from 'react';
import { Chip, Tooltip } from '@mui/material';

/**
 * Standard small badge to mark UI controls that are not implemented/enforced yet.
 * Usage: <NotImplemented label="Not enforced yet" />
 */
export default function NotImplemented({
  label = 'Not implemented yet',
  tooltip = 'This option is not implemented/enforced yet',
  color = 'warning',
  variant = 'outlined',
  size = 'small',
  sx = {},
}) {
  return (
    <Tooltip title={tooltip} arrow>
      <Chip
        label={label}
        color={color}
        variant={variant}
        size={size}
        sx={{ fontSize: '0.7rem', height: '20px', ...sx }}
      />
    </Tooltip>
  );
}
