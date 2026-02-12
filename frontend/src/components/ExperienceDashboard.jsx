import React, { useState, useCallback } from 'react';
import { useQuery, useQueryClient } from 'react-query';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  alpha,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  IconButton,
  Link,
  Stack,
  Tooltip,
  Typography,
  useTheme,
} from '@mui/material';
import {
  Add as AddIcon,
  PlayArrow as PlayIcon,
  Refresh as RefreshIcon,
  SmartToy as BotIcon,
} from '@mui/icons-material';
import { formatDistanceToNow } from 'date-fns';
import { experiencesAPI, extractDataFromResponse, formatError } from '../services/api';
import ExperienceRunDialog from './ExperienceRunDialog';

/**
 * Run button with tooltip for experience cards.
 */
const CardRunButton = ({ experience, onRun }) => (
  <Tooltip title={experience.can_run ? 'Run experience' : 'Missing required connections'}>
    <span>
      <IconButton
        size="small"
        color="primary"
        disabled={!experience.can_run}
        onClick={(e) => {
          e.stopPropagation();
          onRun(experience);
        }}
      >
        <PlayIcon fontSize="small" />
      </IconButton>
    </span>
  </Tooltip>
);

/**
 * Code-like data preview block for experience cards.
 */
const CardDataPreview = ({ experience, dataPreview, theme }) => (
  <>
    <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 0.5 }}>
      {experience.experience_name.toLowerCase().replace(/\s+/g, '_')}
    </Typography>
    <Typography
      variant="body2"
      color="text.secondary"
      sx={{
        fontFamily: 'monospace',
        fontSize: '0.75rem',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
        mb: 1.5,
        backgroundColor: alpha(theme.palette.text.primary, 0.05),
        p: 1,
        borderRadius: 1,
      }}
    >
      {dataPreview.length > 100 ? dataPreview.substring(0, 100) + '...' : dataPreview}
    </Typography>
  </>
);

/**
 * Timestamp chip with green status indicator.
 */
const CardTimestamp = ({ relativeTime, theme }) => (
  <Chip
    icon={
      <Box
        sx={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          bgcolor: theme.palette.success.main,
          ml: 1,
        }}
      />
    }
    label={relativeTime}
    size="small"
    sx={{
      bgcolor: 'transparent',
      border: `1px solid ${theme.palette.divider}`,
      color: theme.palette.success.main,
      fontSize: '0.75rem',
      mb: 1.5,
      '& .MuiChip-icon': {
        marginLeft: '8px',
      },
    }}
  />
);

/**
 * Experience result card using theme-aware styling (like QuickStart SectionCard).
 * Shows: bot icon, title, preview text, relative timestamp, and "View Details" link.
 */
const ExperienceResultCard = ({ experience, onClick, onRun }) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const hasResult = !!experience.latest_run_id;
  const promptPreview = experience.prompt_template ? experience.prompt_template.substring(0, 100) : '';
  const dataPreview = experience.result_preview ? experience.result_preview.substring(0, 200) : '';
  const relativeTime = experience.latest_run_finished_at
    ? formatDistanceToNow(new Date(experience.latest_run_finished_at), { addSuffix: true })
    : null;

  const handleClick = () => {
    if (onClick) {
      onClick(experience.experience_id);
    }
  };

  return (
    <Card
      elevation={0}
      sx={{
        height: '100%',
        border: `1px solid ${theme.palette.divider}`,
        transition: 'all 0.2s ease-in-out',
        backgroundColor: 'inherit',
        maxWidth: 360,
        '&:hover': {
          borderColor: theme.palette.primary.main,
          boxShadow: `0 4px 12px ${alpha(theme.palette.primary.main, 0.15)}`,
        },
      }}
    >
      <CardActionArea onClick={handleClick} disabled={!onClick}>
        <CardContent>
          <Box display="flex" alignItems="center" mb={1.5}>
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
              <BotIcon />
            </Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, flex: 1 }}>
              {experience.experience_name}
            </Typography>
            <CardRunButton experience={experience} onRun={onRun} />
          </Box>

          {promptPreview && (
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              {promptPreview}...
            </Typography>
          )}

          {hasResult && dataPreview && (
            <CardDataPreview experience={experience} dataPreview={dataPreview} theme={theme} />
          )}

          {relativeTime && <CardTimestamp relativeTime={relativeTime} theme={theme} />}

          <Box sx={{ display: 'flex', alignItems: 'center', color: 'primary.main' }}>
            <Link
              component="button"
              variant="body2"
              onClick={(e) => {
                e.stopPropagation();
                handleClick();
              }}
              sx={{
                fontWeight: 500,
                cursor: 'pointer',
                textDecoration: 'none',
                '&:hover': { textDecoration: 'underline' },
              }}
            >
              View Details
            </Link>
          </Box>

          {experience.missing_identities?.length > 0 && (
            <Alert
              severity="warning"
              sx={{ mt: 2 }}
              action={
                <Button
                  color="inherit"
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(
                      `/settings/connected-accounts?highlight=${encodeURIComponent(experience.missing_identities.join(','))}`
                    );
                  }}
                >
                  Activate Now
                </Button>
              }
            >
              Missing required connections: {experience.missing_identities.join(', ')}
            </Alert>
          )}
        </CardContent>
      </CardActionArea>
    </Card>
  );
};

/**
 * Empty state shown when user has no experience results.
 */
const DashboardEmptyState = ({ onCreateConversation, createConversationDisabled }) => (
  <Box
    sx={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      textAlign: 'center',
      py: 6,
    }}
  >
    <BotIcon sx={{ fontSize: 80, color: 'grey.300', mb: 2 }} />
    <Typography variant="h6" color="text.secondary" gutterBottom>
      No experiences available
    </Typography>
    <Typography variant="body2" color="text.secondary" sx={{ mb: 3, maxWidth: 400 }}>
      There are no published experiences yet. Start a new chat to begin your conversation.
    </Typography>
    <Button
      variant="contained"
      startIcon={<AddIcon />}
      onClick={onCreateConversation}
      disabled={createConversationDisabled}
    >
      Start New Chat
    </Button>
  </Box>
);

/**
 * Content area rendering loading, error, empty, or card grid states.
 */
const DashboardContent = ({
  isLoading,
  error,
  experiences,
  onCardClick,
  onRun,
  onCreateConversation,
  createConversationDisabled,
}) => (
  <Box sx={{ flex: 1, p: { xs: 2, sm: 3 }, pt: 2 }}>
    {isLoading && (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight={200}>
        <CircularProgress />
      </Box>
    )}

    {error && (
      <Alert severity="error" sx={{ mb: 2 }}>
        {formatError(error)}
      </Alert>
    )}

    {!isLoading && !error && experiences.length === 0 && (
      <DashboardEmptyState
        onCreateConversation={onCreateConversation}
        createConversationDisabled={createConversationDisabled}
      />
    )}

    {!isLoading && !error && experiences.length > 0 && (
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
        {experiences.map((exp) => (
          <ExperienceResultCard key={exp.experience_id} experience={exp} onClick={onCardClick} onRun={onRun} />
        ))}
      </Box>
    )}
  </Box>
);

export default function ExperienceDashboard({ onCreateConversation, createConversationDisabled, onExperienceClick }) {
  const queryClient = useQueryClient();
  const [runDialogExperience, setRunDialogExperience] = useState(null);

  // Fetch user's experience results
  const {
    data: results,
    isLoading,
    error,
    refetch,
  } = useQuery(['my-experience-results'], () => experiencesAPI.getMyResults().then(extractDataFromResponse), {
    staleTime: 0,
    refetchInterval: 60000, // Auto-refresh every minute
  });

  const experiences = results?.experiences || [];
  const scheduledCount = results?.scheduled_count || 0;

  const handleCardClick = (experienceId) => {
    if (onExperienceClick) {
      onExperienceClick(experienceId);
    }
  };

  const handleRun = useCallback((experience) => {
    setRunDialogExperience({ id: experience.experience_id, name: experience.experience_name });
  }, []);

  const handleRunDialogClose = useCallback(() => {
    setRunDialogExperience(null);
    queryClient.invalidateQueries(['my-experience-results']);
  }, [queryClient]);

  return (
    <Box
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'auto',
        bgcolor: 'background.default',
      }}
    >
      {/* Header */}
      <Box sx={{ p: { xs: 2, sm: 3 }, pb: 0 }}>
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            mb: 2,
          }}
        >
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 600 }}>
              Dashboard
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {experiences.length} active results • {scheduledCount} scheduled
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <IconButton onClick={() => refetch()} disabled={isLoading}>
              <RefreshIcon />
            </IconButton>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={onCreateConversation}
              disabled={createConversationDisabled}
            >
              New Chat
            </Button>
          </Stack>
        </Box>
      </Box>

      <DashboardContent
        isLoading={isLoading}
        error={error}
        experiences={experiences}
        onCardClick={handleCardClick}
        onRun={handleRun}
        onCreateConversation={onCreateConversation}
        createConversationDisabled={createConversationDisabled}
      />

      {/* Run Dialog */}
      {runDialogExperience && (
        <ExperienceRunDialog
          key={runDialogExperience.id}
          open={!!runDialogExperience}
          onClose={handleRunDialogClose}
          experienceId={runDialogExperience.id}
          experienceName={runDialogExperience.name}
          steps={[]}
        />
      )}
    </Box>
  );
}
