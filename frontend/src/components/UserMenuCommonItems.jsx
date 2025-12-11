import React from 'react';
import { MenuItem, ListItemIcon, Divider } from '@mui/material';
import { Security as SecurityIcon, SmartToy as ChatIcon, ManageAccounts as AccountsIcon, Settings as SettingsIcon } from '@mui/icons-material';

/**
 * UserMenuCommonItems
 * Shared user menu entries used in both UserLayout and AdminLayout.
 * Props:
 *  - onNavigate: (path: string) => void
 */
export default function UserMenuCommonItems({ onNavigate }) {
  const go = (path) => () => onNavigate && onNavigate(path);
  return (
    <>
      <MenuItem onClick={go('/chat')}>
        <ListItemIcon><ChatIcon fontSize="small" /></ListItemIcon>
        Chat
      </MenuItem>
      <MenuItem onClick={go('/permissions')}>
        <ListItemIcon><SecurityIcon fontSize="small" /></ListItemIcon>
        My Permissions
      </MenuItem>
      <MenuItem onClick={go('/settings/connected-accounts')}>
        <ListItemIcon><AccountsIcon fontSize="small" /></ListItemIcon>
        Plugin Subscriptions
      </MenuItem>
      <MenuItem onClick={go('/settings/preferences')}>
        <ListItemIcon><SettingsIcon fontSize="small" /></ListItemIcon>
        User Preferences
      </MenuItem>
      <Divider />
    </>
  );
}

