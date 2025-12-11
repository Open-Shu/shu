import React from 'react';
import { Menu, MenuItem, ListItemIcon } from '@mui/material';
import LockIcon from '@mui/icons-material/Lock';
import LockOpenIcon from '@mui/icons-material/LockOpen';
import RefreshIcon from '@mui/icons-material/Refresh';

const AutomationMenu = React.memo(function AutomationMenu({
  anchorEl,
  onClose,
  isTitleLocked,
  onUnlock,
  onRunSummaryAndRename,
  disableUnlock,
  disableAutomation,
}) {
  return (
    <Menu
      anchorEl={anchorEl}
      open={Boolean(anchorEl)}
      onClose={onClose}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      transformOrigin={{ vertical: 'top', horizontal: 'left' }}
    >
      {isTitleLocked ? (
        <MenuItem onClick={onUnlock} disabled={disableUnlock}>
          <ListItemIcon>
            <LockOpenIcon fontSize="small" />
          </ListItemIcon>
          Unlock auto-rename
        </MenuItem>
      ) : (
        <MenuItem disabled>
          <ListItemIcon>
            <LockIcon fontSize="small" />
          </ListItemIcon>
          Auto-rename is unlocked
        </MenuItem>
      )}
      <MenuItem onClick={onRunSummaryAndRename} disabled={disableAutomation}>
        <ListItemIcon>
          <RefreshIcon fontSize="small" />
        </ListItemIcon>
        Run summary + rename
      </MenuItem>
    </Menu>
  );
});

export default AutomationMenu;
