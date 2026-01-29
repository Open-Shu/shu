import React from "react";
import { Tooltip, IconButton } from "@mui/material";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";

/**
 * HelpTooltip: standardized help icon with tooltip
 * Props:
 *  - title: string | node (tooltip content)
 *  - placement?: MUI placement (default: 'right')
 *  - ariaLabel?: string
 */
export default function HelpTooltip({
  title,
  placement = "right",
  ariaLabel = "help",
}) {
  return (
    <Tooltip title={title} placement={placement}>
      <IconButton size="small" aria-label={ariaLabel}>
        <HelpOutlineIcon fontSize="small" />
      </IconButton>
    </Tooltip>
  );
}
