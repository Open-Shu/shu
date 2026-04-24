import { Chip, Tooltip } from '@mui/material';

// Intentionally does NOT forward arbitrary props. The badge's identity
// (label, color, variant, aria-label) is fixed — callers only choose the
// tooltip copy so "provider" vs "model" contexts read correctly.
const SystemManagedBadge = ({ tooltipText }) => (
  <Tooltip title={tooltipText}>
    <Chip label="Managed by Shu" variant="outlined" color="primary" size="small" aria-label="Managed by Shu" />
  </Tooltip>
);

export default SystemManagedBadge;
