import React from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  CardActionArea,
  Grid,
  Chip,
  useTheme,
  alpha,
} from '@mui/material';
import {
  RocketLaunch as RocketIcon,
  Storage as KnowledgeBasesIcon,
  Extension as PluginIcon,
  Schedule as FeedsIcon,
  Tune as ModelConfigIcon,
  TextSnippet as PromptsIcon,
  Settings as LLMProvidersIcon,
  People as UsersIcon,
  Groups as GroupsIcon,
  Security as SecurityIcon,
  Palette as BrandingIcon,
  Search as QueryIcon,
  Psychology as LLMTesterIcon,
  HealthAndSafety as HealthIcon,
  ArrowForward as ArrowIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import { getBrandingAppName } from '../utils/constants';
import PageHelpHeader from './PageHelpHeader';

// Section card component for navigation
const SectionCard = ({ title, description, icon, priority, onClick }) => {
  const theme = useTheme();
  
  return (
    <Card
      elevation={0}
      sx={{
        height: '100%',
        border: `1px solid ${theme.palette.divider}`,
        transition: 'all 0.2s ease-in-out',
        '&:hover': {
          borderColor: theme.palette.primary.main,
          boxShadow: `0 4px 12px ${alpha(theme.palette.primary.main, 0.15)}`,
        },
      }}
    >
      <CardActionArea onClick={onClick} sx={{ height: '100%' }}>
        <CardContent sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', mb: 1.5 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 36,
                height: 36,
                borderRadius: 1,
                backgroundColor: alpha(theme.palette.primary.main, 0.1),
                color: theme.palette.primary.main,
                mr: 1.5,
              }}
            >
              {icon}
            </Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, flex: 1 }}>
              {title}
            </Typography>
            {priority && (
              <Chip
                label={priority}
                size="small"
                color={priority === 'Start Here' ? 'primary' : 'default'}
                sx={{ fontSize: '0.7rem', height: 20 }}
              />
            )}
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ flex: 1, lineHeight: 1.5 }}>
            {description}
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', mt: 1.5, color: 'primary.main' }}>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              Open
            </Typography>
            <ArrowIcon fontSize="small" sx={{ ml: 0.5 }} />
          </Box>
        </CardContent>
      </CardActionArea>
    </Card>
  );
};

const QuickStart = () => {
  const navigate = useNavigate();
  const { branding } = useAppTheme();
  const appDisplayName = getBrandingAppName(branding);

  const gettingStartedSections = [
    {
      title: 'Model Configurations',
      description: 'Configure which AI models power your assistant. Set up providers like OpenAI, Anthropic, or local models via Ollama.',
      icon: <ModelConfigIcon />,
      path: '/admin/model-configurations',
      priority: 'Start Here',
    },
    {
      title: 'Knowledge Bases',
      description: 'Create knowledge bases to store and organize your documents. Enable RAG (Retrieval-Augmented Generation) for context-aware responses.',
      icon: <KnowledgeBasesIcon />,
      path: '/admin/knowledge-bases',
      priority: 'Step 2',
    },
    {
      title: 'Plugins',
      description: 'Extend functionality with plugins for email, calendar, drive, and more. Enable plugins to connect external services to your assistant.',
      icon: <PluginIcon />,
      path: '/admin/plugins',
      priority: 'Step 3',
    },
    {
      title: 'Plugin Feeds',
      description: 'Configure automated data synchronization. Feeds pull data from connected services on a schedule into your knowledge bases.',
      icon: <FeedsIcon />,
      path: '/admin/feeds',
      priority: 'Step 4',
    },
  ];

  const advancedSections = [
    {
      title: 'Prompts',
      description: 'Manage system prompts that define your assistant\'s behavior, personality, and response style.',
      icon: <PromptsIcon />,
      path: '/admin/prompts',
    },
    {
      title: 'LLM Providers',
      description: 'Configure API connections to LLM providers. Set up API keys, endpoints, and provider-specific settings.',
      icon: <LLMProvidersIcon />,
      path: '/admin/llm-providers',
    },
    {
      title: 'Branding',
      description: 'Customize the look and feel of your application. Set logos, colors, and display names.',
      icon: <BrandingIcon />,
      path: '/admin/branding',
    },
  ];

  const accessControlSections = [
    {
      title: 'User Management',
      description: 'Manage user accounts, roles, and authentication. Control who can access the system.',
      icon: <UsersIcon />,
      path: '/admin/users',
    },
    {
      title: 'User Groups',
      description: 'Organize users into groups for easier permission management and access control.',
      icon: <GroupsIcon />,
      path: '/admin/user-groups',
    },
    {
      title: 'KB Permissions',
      description: 'Control access to knowledge bases. Define which users or groups can view or edit each KB.',
      icon: <SecurityIcon />,
      path: '/admin/kb-permissions',
    },
  ];

  const toolsSections = [
    {
      title: 'Query Tester',
      description: 'Test vector search and retrieval against your knowledge bases. Debug and tune search quality.',
      icon: <QueryIcon />,
      path: '/admin/query-tester',
    },
    {
      title: 'LLM Tester',
      description: 'Send test prompts directly to configured LLM providers. Verify model responses and behavior.',
      icon: <LLMTesterIcon />,
      path: '/admin/llm-tester',
    },
    {
      title: 'Health Monitor',
      description: 'Monitor system health, database connectivity, and service status in real-time.',
      icon: <HealthIcon />,
      path: '/admin/health',
    },
  ];

  const renderSection = (title, sections, columns = 4) => (
    <Box sx={{ mb: 4 }}>
      <Typography variant="h6" sx={{ mb: 2, fontWeight: 600 }}>
        {title}
      </Typography>
      <Grid container spacing={2}>
        {sections.map((section) => (
          <Grid item xs={12} sm={6} md={12 / columns} key={section.path}>
            <SectionCard
              {...section}
              onClick={() => navigate(section.path)}
            />
          </Grid>
        ))}
      </Grid>
    </Box>
  );

  return (
    <Box>
      <PageHelpHeader
        title={`Welcome to ${appDisplayName}`}
        description={`This Quick Start guide will help you set up and configure ${appDisplayName}. Follow the steps below to get your AI assistant up and running. Each section includes detailed help once you navigate there.`}
        icon={<RocketIcon />}
        tips={[
          'Start by configuring your AI model in Model Configurations - this powers all AI interactions',
          'Create a Knowledge Base to store documents your assistant can reference',
          'Enable Plugins to connect external services like email, calendar, and cloud storage',
          'Set up Plugin Feeds to automatically sync data from connected services',
          'Use the Query Tester and LLM Tester to verify everything is working correctly',
        ]}
        defaultExpanded={true}
      />

      {renderSection('Getting Started', gettingStartedSections, 4)}
      {renderSection('Configuration', advancedSections, 3)}
      {renderSection('Access Control', accessControlSections, 3)}
      {renderSection('Tools & Testing', toolsSections, 3)}

      <Box sx={{ mt: 4, p: 2, backgroundColor: 'action.hover', borderRadius: 2 }}>
        <Typography variant="subtitle2" color="text.secondary">
          Key Concepts
        </Typography>
        <Grid container spacing={2} sx={{ mt: 1 }}>
          <Grid item xs={12} md={4}>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>Knowledge Base (KB)</Typography>
            <Typography variant="body2" color="text.secondary">
              A searchable collection of documents. Used for RAG to give your AI context about your data.
            </Typography>
          </Grid>
          <Grid item xs={12} md={4}>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>Plugin</Typography>
            <Typography variant="body2" color="text.secondary">
              An extension that adds capabilities like email reading, calendar access, or web search.
            </Typography>
          </Grid>
          <Grid item xs={12} md={4}>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>Feed</Typography>
            <Typography variant="body2" color="text.secondary">
              An automated job that runs a plugin operation on a schedule to sync data into a KB.
            </Typography>
          </Grid>
        </Grid>
      </Box>
    </Box>
  );
};

export default QuickStart;

