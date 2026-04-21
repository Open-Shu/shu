import React from 'react';
import { Chip, Tooltip } from '@mui/material';

const SystemManagedBadge = ({ tooltipText, ...chipProps }) => (
  <Tooltip title={tooltipText}>
    <Chip
      label="Managed by Shu"
      variant="outlined"
      color="primary"
      size="small"
      aria-label="Managed by Shu"
      {...chipProps}
    />
  </Tooltip>
);

export default SystemManagedBadge;
