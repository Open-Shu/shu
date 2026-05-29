import {
  Box,
  Card,
  CardContent,
  Tab,
  Tabs,
  Typography,
  Paper,
  List,
  ListItemIcon,
  ListItemText,
  ListItemButton,
  useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
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
  const theme = useTheme();
  // <sm: phone portrait; swap the 300px sidebar for top Tabs so the
  // content pane gets the full viewport width.
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));

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
    <Box sx={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', height: '100%', overflow: 'hidden' }}>
      {/* Desktop sidebar */}
      {!isMobile && (
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
      )}

      {/* Mobile top tabs */}
      {isMobile && (
        <Paper square sx={{ borderBottom: 1, borderColor: 'divider', flexShrink: 0 }}>
          <Tabs value={activeSection} onChange={(_e, newSection) => handleNavigation(newSection)} variant="fullWidth">
            {menuItems.map((item) => (
              <Tab key={item.path} value={item.path} label={item.text} icon={item.icon} iconPosition="start" />
            ))}
          </Tabs>
        </Paper>
      )}

      {/* Main Content */}
      <Box
        sx={{
          flexGrow: 1,
          bgcolor: 'background.default',
          p: { xs: 1.5, sm: 3 },
          overflow: 'auto',
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
