import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Divider, Menu, MenuItem, ListItemIcon } from '@mui/material';
import LockIcon from '@mui/icons-material/Lock';
import LockOpenIcon from '@mui/icons-material/LockOpen';
import RefreshIcon from '@mui/icons-material/Refresh';
import DashboardIcon from '@mui/icons-material/Dashboard';

const AutomationMenu = React.memo(function AutomationMenu({
  anchorEl,
  onClose,
  isTitleLocked,
  onUnlock,
  onRunSummaryAndRename,
  disableUnlock,
  disableAutomation,
}) {
  const navigate = useNavigate();

  const handleDashboardClick = () => {
    onClose();
    navigate('/dashboard');
  };

  return (
    <Menu
      anchorEl={anchorEl}
      open={Boolean(anchorEl)}
      onClose={onClose}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      transformOrigin={{ vertical: 'top', horizontal: 'left' }}
    >
      <MenuItem onClick={handleDashboardClick}>
        <ListItemIcon>
          <DashboardIcon fontSize="small" />
        </ListItemIcon>
        Dashboard
      </MenuItem>
      <Divider />
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
