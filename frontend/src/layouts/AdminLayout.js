


import {
  Box,
  Toolbar,
  Typography,
  Drawer,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  ListItemButton,
  Divider
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import {
  Dashboard as DashboardIcon,
  Storage as KnowledgeBasesIcon,
  TextSnippet as PromptsIcon,
  Search as QueryTesterIcon,
  Psychology as LLMTesterIcon,
  HealthAndSafety as HealthIcon,
  Settings as LLMProvidersIcon,
  Tune as ModelConfigIcon,
  People as UsersIcon,
  Groups as PeopleIcon,
  Security as SecurityIcon,
  AdminPanelSettings as AdminIcon,
  Extension as ExtensionIcon,
  Schedule as ScheduleIcon,
  Palette as BrandingIcon,
} from '@mui/icons-material';

import { useNavigate, useLocation } from 'react-router-dom';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import { getBrandingAppName, getBrandingLogoUrl } from '../utils/constants';
import TopBar from '../components/layout/TopBar.jsx';


const DRAWER_WIDTH = 280;

const AdminLayout = ({ children }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { branding, theme: appTheme } = useAppTheme();
  const appDisplayName = getBrandingAppName(branding);
  const logoUrl = getBrandingLogoUrl(branding);
  const primaryMain = appTheme.palette.primary.main;



  const adminMenuItems = [
    { text: 'Dashboard', icon: <DashboardIcon />, path: '/admin/dashboard' },
    { text: 'Model Configurations', icon: <ModelConfigIcon />, path: '/admin/model-configurations' },
    { text: 'Knowledge Bases', icon: <KnowledgeBasesIcon />, path: '/admin/knowledge-bases' },
    { text: 'Prompts', icon: <PromptsIcon />, path: '/admin/prompts' },
    { text: 'Plugins', icon: <ExtensionIcon />, path: '/admin/plugins' },
    { text: 'Plugin Feeds', icon: <ScheduleIcon />, path: '/admin/feeds' },    
    { text: 'Query Tester', icon: <QueryTesterIcon />, path: '/admin/query-tester' },
    { text: 'LLM Tester', icon: <LLMTesterIcon />, path: '/admin/llm-tester' },
    { text: 'Health Monitor', icon: <HealthIcon />, path: '/admin/health' },
  ];

  const systemMenuItems = [
    { text: 'LLM Providers', icon: <LLMProvidersIcon />, path: '/admin/llm-providers' },
    { text: 'Branding', icon: <BrandingIcon />, path: '/admin/branding' },
  ];

  const rbacMenuItems = [
    { text: 'User Management', icon: <UsersIcon />, path: '/admin/users' },
    { text: 'User Groups', icon: <PeopleIcon />, path: '/admin/user-groups' },
    { text: 'KB Permissions', icon: <SecurityIcon />, path: '/admin/kb-permissions' },
  ];

  const isActive = (path) => location.pathname === path;

  return (
    <Box sx={{ display: 'flex' }}>
      {/* Shared TopBar */}
      <TopBar
        sectionTitle="Admin Panel"
        sectionIcon={<AdminIcon />}
        leftOffset={DRAWER_WIDTH + 16}
        appBarPosition="fixed"
        fixedOverDrawer
        showAdminLink={false}
      />

      {/* Sidebar */}
      <Drawer
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': {
            width: DRAWER_WIDTH,
            boxSizing: 'border-box',
            height: '100vh', // Full viewport height
            display: 'flex',
            flexDirection: 'column',
          },
        }}
        variant="permanent"
        anchor="left"
      >
        <Toolbar />
        <Divider />

        {/* Scrollable Menu Content */}
        <Box sx={{ flex: 1, overflow: 'auto' }}>
          {/* Power User Features */}
          <List>
          <ListItem>
            <Typography
              variant="overline"
              sx={{
                fontSize: '0.75rem',
                color: 'primary.main',
                fontWeight: 600,
                letterSpacing: '0.1em'
              }}
            >
              KNOWLEDGE MANAGEMENT
            </Typography>
          </ListItem>
          {adminMenuItems.map((item) => (
            <ListItemButton
              key={item.text}
              selected={isActive(item.path)}
              onClick={() => navigate(item.path)}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.text} />
            </ListItemButton>
          ))}
        </List>

        <Divider />

        {/* System Configuration */}
        <List>
          <ListItem>
            <Typography
              variant="overline"
              sx={{
                fontSize: '0.75rem',
                color: 'primary.main',
                fontWeight: 600,
                letterSpacing: '0.1em'
              }}
            >
              System Configuration
            </Typography>
          </ListItem>
          {systemMenuItems.map((item) => (
            <ListItemButton
              key={item.text}
              selected={isActive(item.path)}
              onClick={() => navigate(item.path)}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.text} />
            </ListItemButton>
          ))}

          {/* RBAC Management */}
          <ListItem>
            <Typography
              variant="overline"
              sx={{
                fontSize: '0.75rem',
                color: 'primary.main',
                fontWeight: 600,
                letterSpacing: '0.1em'
              }}
            >
              Access Control
            </Typography>
          </ListItem>
          {rbacMenuItems.map((item) => (
            <ListItemButton
              key={item.text}
              selected={isActive(item.path)}
              onClick={() => navigate(item.path)}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.text} />
            </ListItemButton>
          ))}
        </List>
        </Box>

        {/* Branding Logo at Bottom - Full Width */}
        <Box
          sx={{
            mt: 'auto', // Push to bottom
            p: 2,
            backgroundColor: alpha(primaryMain, 0.00),
            borderTop: `1px solid ${alpha(primaryMain, 0.1)}`,
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center'
          }}
        >
          <img
            src={logoUrl}
            alt={appDisplayName}
            style={{
              height: '60px', // Fixed height for normal proportions
              width: 'auto', // Maintain aspect ratio
              maxWidth: '100%' // Don't exceed container width
            }}
          />
        </Box>
      </Drawer>

      {/* Main Content */}
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          bgcolor: 'background.default',
          p: 3
        }}
      >
        <Toolbar />
        {children}
      </Box>
    </Box>
  );
};

export default AdminLayout;
