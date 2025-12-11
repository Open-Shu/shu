import React from 'react';
import {
  AppBar,
  Toolbar,
  Typography,
  IconButton,
  Box,
  Menu,
  MenuItem,
  Divider,
  ListItemIcon
} from '@mui/material';
import {
  Logout as LogoutIcon,
  Person as PersonIcon,
  AdminPanelSettings as AdminIcon
} from '@mui/icons-material';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { useTheme as useAppTheme } from '../../contexts/ThemeContext';
import { getBrandingAppName, getBrandingFaviconUrl } from '../../utils/constants';
import UserMenuCommonItems from '../UserMenuCommonItems.jsx';
import UserAvatar from '../shared/UserAvatar.jsx';
import useVersion from '../../hooks/useVersion';

/**
 * Reusable top application bar shared across layouts.
 * Props:
 * - sectionTitle?: string (optional label to render near leftOffset)
 * - sectionIcon?: ReactNode (optional icon to show before title)
 * - leftOffset?: number (px from left edge where section title should align)
 * - appBarPosition?: 'fixed' | 'static' (default 'fixed')
 * - fixedOverDrawer?: boolean (if true, zIndex above drawer)
 * - showAdminLink?: boolean (if true, include Admin Panel link)
 */
const TopBar = ({
  sectionTitle,
  sectionIcon,
  leftOffset = 0,
  appBarPosition = 'fixed',
  fixedOverDrawer = false,
  showAdminLink = false,
}) => {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { branding } = useAppTheme();
  const appDisplayName = getBrandingAppName(branding);
  const faviconUrl = getBrandingFaviconUrl(branding);
  const { displayVersion } = useVersion();

  const [anchorEl, setAnchorEl] = React.useState(null);
  const handleUserMenuOpen = (e) => setAnchorEl(e.currentTarget);
  const handleUserMenuClose = () => setAnchorEl(null);
  const handleLogout = () => { handleUserMenuClose(); logout(); };

  const handleAdminPanel = () => {
    handleUserMenuClose();
    navigate('/admin/dashboard');
  };

  const isAdmin = user?.role === 'admin';
  const isPowerUser = user?.role === 'power_user';
  const canAccessAdmin = isAdmin || isPowerUser;

  return (
    <AppBar
      position={appBarPosition}
      sx={{
        bgcolor: 'primary.main',
        ...(fixedOverDrawer ? { zIndex: (theme) => theme.zIndex.drawer + 1 } : {}),
      }}
    >
      <Toolbar sx={{ position: 'relative' }}>
        {/* Left: Brand link to chat */}
        <Box
          component={RouterLink}
          to="/chat"
          sx={{
            display: 'flex',
            alignItems: 'center',
            mr: 2,
            textDecoration: 'none',
            color: 'inherit',
            cursor: 'pointer'
          }}
        >
          <img src={faviconUrl} alt={appDisplayName} style={{ height: 48, width: 'auto', marginRight: 8 }} />
          <Typography variant="h2" sx={{ fontWeight: 600, color: '#000000ff' }}>{appDisplayName || ''}</Typography>
        </Box>

        {/* Optional section title aligned with content start */}
        {sectionTitle ? (
          <Box sx={{ position: 'absolute', left: `${leftOffset}px`, display: 'flex', alignItems: 'center', height: '100%' }}>
            {sectionIcon ? <Box sx={{ mr: 1, display: 'flex', alignItems: 'center' }}>{sectionIcon}</Box> : null}
            <Typography variant="h6" sx={{ color: '#FFFFFF', fontWeight: 600 }}>{sectionTitle}</Typography>
          </Box>
        ) : null}

        <Box sx={{ flexGrow: 1 }} />

        {/* Right: user menu */}
        <IconButton
          size="large"
          edge="end"
          onClick={handleUserMenuOpen}
          className="user-menu"
          sx={{ color: '#FFFFFF', '&:hover': { backgroundColor: 'rgba(255,255,255,0.1)' } }}
        >
          <UserAvatar user={user} size={32} fallbackChar={user?.name?.charAt(0) || 'U'} />
        </IconButton>

        <Menu id="user-menu" anchorEl={anchorEl} open={Boolean(anchorEl)} onClose={handleUserMenuClose}>
          <MenuItem disabled>
            <ListItemIcon>
              <PersonIcon fontSize="small" />
            </ListItemIcon>
            <Box>
              <Typography variant="body2" fontWeight="bold">{user?.name}</Typography>
              <Typography variant="caption" color="text.secondary">{user?.email}</Typography>
            </Box>
          </MenuItem>
          <Divider />
          <UserMenuCommonItems onNavigate={(path) => { handleUserMenuClose(); navigate(path); }} />
          {showAdminLink && canAccessAdmin && (
            <MenuItem onClick={handleAdminPanel}>
              <ListItemIcon>
                <AdminIcon fontSize="small" />
              </ListItemIcon>
              Admin Panel
            </MenuItem>
          )}
          <MenuItem onClick={handleLogout}>
            <ListItemIcon>
              <LogoutIcon fontSize="small" />
            </ListItemIcon>
            Logout
          </MenuItem>
          {displayVersion && (
            <MenuItem disabled>
              <Typography
                variant="caption"
                sx={{
                  color: 'text.disabled',
                  fontSize: '0.7rem',
                  fontStyle: 'italic'
                }}
              >
                {displayVersion}
              </Typography>
            </MenuItem>
          )}
        </Menu>
      </Toolbar>
    </AppBar>
  );
};

export default TopBar;
