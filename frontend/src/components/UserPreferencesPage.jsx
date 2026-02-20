import {
  Box,
  Card,
  CardContent,
  Typography,
  Paper,
  List,
  ListItemIcon,
  ListItemText,
  ListItemButton,
} from '@mui/material';
import { Settings as SettingsIcon, Lock as LockIcon } from '@mui/icons-material';
import { useNavigate, useParams, Navigate } from 'react-router-dom';
import GeneralPreferencesSection from './GeneralPreferencesSection';
import ChangePasswordForm from './ChangePasswordForm';

const menuItems = [
  { text: 'General', icon: <SettingsIcon />, path: 'general' },
  { text: 'Security', icon: <LockIcon />, path: 'security' },
];

export default function UserPreferencesPage() {
  const navigate = useNavigate();
  const { section } = useParams();

  const activeSection = section || 'general';
  const activeItem = menuItems.find((item) => item.path === activeSection);

  const handleNavigation = (path) => {
    if (path !== activeSection) {
      navigate(`/settings/preferences/${path}`);
    }
  };

  const getContent = () => {
    if (activeSection === 'general') {
      return <GeneralPreferencesSection />;
    }
    if (activeSection === 'security') {
      return (
        <>
          <Typography variant="h6" gutterBottom sx={{ mb: 3 }}>
            Change Password
          </Typography>
          <ChangePasswordForm />
        </>
      );
    }
    return <Navigate to="/settings/preferences/general" replace />;
  };

  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Sidebar */}
      <Paper
        sx={{
          width: 300,
          minWidth: 300,
          maxWidth: 300,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 0,
          borderRight: 1,
          borderColor: 'divider',
        }}
      >
        <List>
          {menuItems.map((item) => (
            <ListItemButton
              key={item.path}
              selected={activeSection === item.path}
              onClick={() => handleNavigation(item.path)}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.text} />
            </ListItemButton>
          ))}
        </List>
      </Paper>

      {/* Main Content */}
      <Box
        sx={{
          flexGrow: 1,
          bgcolor: 'background.default',
          p: 3,
        }}
      >
        <Typography variant="h4" component="h1" gutterBottom>
          {activeItem?.text || 'Preferences'}
        </Typography>

        <Card>
          <CardContent>{getContent()}</CardContent>
        </Card>
      </Box>
    </Box>
  );
}
