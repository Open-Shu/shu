import { useState, useEffect } from 'react';
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
  CircularProgress,
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
  WbSunny as BriefingIcon,
  CheckCircle as CheckIcon,
  Description as DocumentsIcon,
  AutoAwesome as ExperiencesIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import { getBrandingAppName } from '../utils/constants';
import PageHelpHeader from './PageHelpHeader';
import { setupAPI, extractDataFromResponse } from '../services/api';

// Section card component for navigation
const SectionCard = ({ title, description, icon, priority, completed, onClick }) => {
  const theme = useTheme();

  // Determine chip display: completed takes precedence over priority
  const renderChip = () => {
    if (completed) {
      return (
        <Chip
          icon={<CheckIcon sx={{ fontSize: '0.9rem' }} />}
          label="Done"
          size="small"
          color="success"
          sx={{ fontSize: '0.7rem', height: 20 }}
        />
      );
    }
    if (priority) {
      return (
        <Chip
          label={priority}
          size="small"
          color={priority === 'Start Here' ? 'primary' : 'default'}
          sx={{ fontSize: '0.7rem', height: 20 }}
        />
      );
    }
    return null;
  };

  return (
    <Card
      elevation={0}
      sx={{
        height: '100%',
        border: `1px solid ${completed ? theme.palette.success.main : theme.palette.divider}`,
        transition: 'all 0.2s ease-in-out',
        backgroundColor: completed ? alpha(theme.palette.success.main, 0.03) : 'inherit',
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
                backgroundColor: completed
                  ? alpha(theme.palette.success.main, 0.1)
                  : alpha(theme.palette.primary.main, 0.1),
                color: completed ? theme.palette.success.main : theme.palette.primary.main,
                mr: 1.5,
              }}
            >
              {icon}
            </Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, flex: 1 }}>
              {title}
            </Typography>
            {renderChip()}
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ flex: 1, lineHeight: 1.5 }}>
            {description}
          </Typography>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              mt: 1.5,
              color: 'primary.main',
            }}
          >
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

  // Setup status state
  const [setupStatus, setSetupStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(true);

  // Fetch setup status on mount
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const response = await setupAPI.getStatus();
        const status = extractDataFromResponse(response);
        setSetupStatus(status);
      } catch (error) {
        console.error('Failed to fetch setup status:', error);
        // Silently fail - status is optional enhancement
      } finally {
        setStatusLoading(false);
      }
    };
    fetchStatus();
  }, []);

  // Map setup status to section keys
  const getCompletionStatus = (statusKey) => {
    if (!setupStatus) {
      return false;
    }
    return setupStatus[statusKey] === true;
  };

  const gettingStartedSections = [
    {
      title: 'LLM Providers',
      description:
        'Configure API connections to LLM providers (OpenAI, Anthropic, Ollama, etc.). Set up API keys and endpoints first.',
      icon: <LLMProvidersIcon />,
      path: '/admin/llm-providers',
      priority: 'Start Here',
      statusKey: 'llm_provider_configured',
    },
    {
      title: 'Model Configurations',
      description:
        'Create model configurations that define which AI models power your assistant. Requires an LLM Provider.',
      icon: <ModelConfigIcon />,
      path: '/admin/model-configurations',
      priority: 'Step 2',
      statusKey: 'model_configuration_created',
    },
    {
      title: 'Knowledge Bases',
      description:
        'Create knowledge bases to store and organize your documents. Enable RAG (Retrieval-Augmented Generation) for context-aware responses.',
      icon: <KnowledgeBasesIcon />,
      path: '/admin/knowledge-bases',
      priority: 'Step 3',
      statusKey: 'knowledge_base_created',
    },
    {
      title: 'Add Documents',
      description: 'Upload documents to your knowledge bases. These will be indexed for semantic search and retrieval.',
      icon: <DocumentsIcon />,
      path: '/admin/knowledge-bases?action=add-documents',
      priority: 'Step 4',
      statusKey: 'documents_added',
    },
    {
      title: 'Plugins',
      description:
        'Extend functionality with plugins. Connect external services like Gmail, Google Drive, and Calendar.',
      icon: <PluginIcon />,
      path: '/admin/plugins',
      priority: 'Step 5',
      statusKey: 'plugins_enabled',
    },
    {
      title: 'Plugin Feeds',
      description:
        'Configure automated data synchronization. Feeds pull data from connected services on a schedule into your knowledge bases.',
      icon: <FeedsIcon />,
      path: '/admin/feeds',
      priority: 'Step 6',
      statusKey: 'plugin_feed_created',
    },
    {
      title: 'Experiences',
      description:
        'Create automated workflows that combine plugins, knowledge bases, and AI synthesis. Build signature experiences like Morning Briefing.',
      icon: <ExperiencesIcon />,
      path: '/admin/experiences',
      priority: 'Step 7',
      statusKey: 'experience_created',
    },
  ];

  const advancedSections = [
    {
      title: 'Prompts',
      description: "Manage system prompts that define your assistant's behavior, personality, and response style.",
      icon: <PromptsIcon />,
      path: '/admin/prompts',
    },
    {
      title: 'Branding',
      description: 'Customize the look and feel of your application. Set favicons, colors, and display names.',
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
      title: 'Morning Briefing',
      description:
        'Run an AI-powered daily briefing that summarizes your calendar, email, and chat. Experimental demo feature.',
      icon: <BriefingIcon />,
      path: '/admin/briefing',
    },
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

  // Calculate progress for Getting Started section
  const completedSteps = gettingStartedSections.filter((s) => s.statusKey && getCompletionStatus(s.statusKey)).length;
  const totalSteps = gettingStartedSections.length;

  const renderSection = (title, sections, columns = 4, showProgress = false) => (
    <Box sx={{ mb: 4 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
        <Typography variant="h6" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        {showProgress && !statusLoading && (
          <Chip
            label={`${completedSteps} of ${totalSteps} complete`}
            size="small"
            color={completedSteps === totalSteps ? 'success' : 'default'}
            sx={{ ml: 2, fontSize: '0.75rem' }}
          />
        )}
        {showProgress && statusLoading && <CircularProgress size={16} sx={{ ml: 2 }} />}
      </Box>
      <Grid container spacing={2}>
        {sections.map((section, index) => (
          <Grid item xs={12} sm={6} md={12 / columns} key={section.path + index}>
            <SectionCard
              {...section}
              completed={section.statusKey ? getCompletionStatus(section.statusKey) : false}
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
          'Start by configuring an LLM Provider with your API key (OpenAI, Anthropic, Ollama, etc.)',
          'Create a Model Configuration to define which AI model powers your assistant',
          'Create a Knowledge Base to store documents your assistant can reference',
          'Enable Plugins to connect external services and power automated data feeds',
          'Use the Query Tester and LLM Tester to verify everything is working correctly',
        ]}
        defaultExpanded={true}
      />

      {renderSection('Getting Started', gettingStartedSections, 3, true)}
      {renderSection('Configuration', advancedSections, 3)}
      {renderSection('Access Control', accessControlSections, 3)}
      {renderSection('Tools & Testing', toolsSections, 4)}

      <Box sx={{ mt: 4, p: 2, backgroundColor: 'action.hover', borderRadius: 2 }}>
        <Typography variant="subtitle2" color="text.secondary">
          Key Concepts
        </Typography>
        <Grid container spacing={2} sx={{ mt: 1 }}>
          <Grid item xs={12} md={4}>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              Knowledge Base (KB)
            </Typography>
            <Typography variant="body2" color="text.secondary">
              A searchable collection of documents. Used for RAG to give your AI context about your data.
            </Typography>
          </Grid>
          <Grid item xs={12} md={4}>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              Plugin
            </Typography>
            <Typography variant="body2" color="text.secondary">
              An extension that adds capabilities like email reading, calendar access, or web search.
            </Typography>
          </Grid>
          <Grid item xs={12} md={4}>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              Feed
            </Typography>
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
