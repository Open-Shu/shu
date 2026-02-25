import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from 'react-query';
import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  InputAdornment,
  Paper,
  Snackbar,
  TextField,
  Typography,
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  Chat as ChatIcon,
  PlayArrow as PlayArrowIcon,
  Send as SendIcon,
} from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import { chatAPI, experiencesAPI, extractDataFromResponse, formatError } from '../services/api';
import ExperienceRunDialog from '../components/ExperienceRunDialog';
import MarkdownRenderer from '../components/shared/MarkdownRenderer';
import { formatDateTimeFull } from '../utils/timezoneFormatter';
import log from '../utils/log';

/**
 * Full-width page displaying a single experience result.
 * Accessible via /dashboard/experience/:experienceId route.
 */
const ExperienceDetailPage = () => {
  const { experienceId } = useParams();
  const navigate = useNavigate();
  const theme = useTheme();
  const queryClient = useQueryClient();

  // State for conversation creation
  const [isCreatingConversation, setIsCreatingConversation] = useState(false);
  const [initialQuestion, setInitialQuestion] = useState('');
  const [runDialogOpen, setRunDialogOpen] = useState(false);
  const [errorSnackbar, setErrorSnackbar] = useState({
    open: false,
    message: '',
  });

  // Detect dark mode from theme
  const isDarkMode = theme.palette.mode === 'dark';

  // Fetch experience details from my-results endpoint
  const {
    data: results,
    isLoading,
    error,
  } = useQuery(['my-experience-results'], () => experiencesAPI.getMyResults().then(extractDataFromResponse), {
    staleTime: 0,
  });

  const handleBack = () => {
    navigate('/dashboard');
  };

  const handleStartConversation = async (question) => {
    if (!experience?.latest_run_id) {
      setErrorSnackbar({
        open: true,
        message: 'No result content available to start conversation',
      });
      return;
    }

    try {
      setIsCreatingConversation(true);

      const response = await chatAPI.createConversationFromExperience(experience.latest_run_id);
      const conversation = extractDataFromResponse(response);

      log.info('Started conversation from experience', {
        conversationId: conversation.id,
        runId: experience.latest_run_id,
        experienceId: experience.experience_id,
      });

      navigate(`/chat?conversationId=${conversation.id}&initialMessage=${encodeURIComponent(question)}`);
    } catch (error) {
      log.error('Failed to start conversation from experience:', error);
      setErrorSnackbar({
        open: true,
        message: formatError(error) || 'Failed to start conversation. Please try again.',
      });
    } finally {
      setIsCreatingConversation(false);
    }
  };

  const handleCloseErrorSnackbar = () => {
    setErrorSnackbar({ open: false, message: '' });
  };

  // Find the specific experience from results
  const experiences = results?.experiences || [];
  const experience = experiences.find((exp) => exp.experience_id === experienceId);

  return (
    <Box
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Header with back button */}
      <Paper
        sx={{
          p: 2,
          borderRadius: 0,
          borderBottom: 1,
          borderColor: 'divider',
          display: 'flex',
          alignItems: 'center',
          gap: 2,
        }}
      >
        <IconButton onClick={handleBack} aria-label="Back to dashboard">
          <ArrowBackIcon />
        </IconButton>
        <Typography variant="h6" sx={{ fontWeight: 600, flex: 1 }}>
          {experience?.experience_name || 'Experience Details'}
        </Typography>
        {experience && (
          <>
            <Button
              variant="outlined"
              startIcon={<PlayArrowIcon />}
              disabled={!experience.can_run}
              onClick={() => setRunDialogOpen(true)}
            >
              Run
            </Button>
          </>
        )}
      </Paper>

      {/* Content */}
      <Box sx={{ flex: 1, overflow: 'auto', p: { xs: 2, sm: 3 } }}>
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

        {!isLoading && !error && !experience && (
          <Alert severity="warning">Experience not found or no results available.</Alert>
        )}

        {!isLoading && !error && experience && experience.missing_identities?.length > 0 && (
          <Alert
            severity="warning"
            sx={{ mb: 2 }}
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() =>
                  navigate(
                    `/settings/connected-accounts?highlight=${encodeURIComponent(experience.missing_identities.join(','))}`
                  )
                }
              >
                Activate Now
              </Button>
            }
          >
            Missing required connections: {experience.missing_identities.join(', ')}
          </Alert>
        )}

        {!isLoading && !error && experience && (
          <Box
            sx={{
              bgcolor: 'background.paper',
              borderRadius: 2,
              p: 3,
            }}
          >
            {/* Experience metadata */}
            {experience.latest_run_finished_at && (
              <Box sx={{ mb: 3, pb: 2, borderBottom: 1, borderColor: 'divider' }}>
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  <strong>Generated:</strong>{' '}
                  {experience.trigger_config?.timezone
                    ? formatDateTimeFull(experience.latest_run_finished_at, experience.trigger_config.timezone)
                    : new Date(experience.latest_run_finished_at).toLocaleString()}
                </Typography>
              </Box>
            )}

            <MarkdownRenderer content={experience.result_preview} isDarkMode={isDarkMode} />
          </Box>
        )}

        {/* Question input — inline below output */}
        {!isLoading && !error && experience?.latest_run_id && (
          <Paper
            elevation={0}
            sx={{
              mt: 3,
              p: 2,
              border: 2,
              borderColor: 'primary.main',
              borderRadius: 2,
              bgcolor: (t) =>
                t.palette.mode === 'dark' ? `${t.palette.primary.main}14` : `${t.palette.primary.main}0d`,
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <ChatIcon fontSize="small" color="primary" />
              <Typography variant="body2" color="primary" sx={{ fontWeight: 600 }}>
                Ask a follow-up question
              </Typography>
            </Box>
            <TextField
              fullWidth
              multiline
              maxRows={4}
              placeholder="What would you like to know about this?"
              value={initialQuestion}
              onChange={(e) => setInitialQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && initialQuestion.trim()) {
                  e.preventDefault();
                  handleStartConversation(initialQuestion.trim());
                }
              }}
              disabled={isCreatingConversation}
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      onClick={() => handleStartConversation(initialQuestion.trim())}
                      disabled={!initialQuestion.trim() || isCreatingConversation}
                      color="primary"
                      aria-label={isCreatingConversation ? 'Sending…' : 'Send question'}
                    >
                      {isCreatingConversation ? <CircularProgress size={20} /> : <SendIcon />}
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
          </Paper>
        )}
      </Box>

      {/* Run Dialog */}
      {runDialogOpen && (
        <ExperienceRunDialog
          key={experienceId}
          open={runDialogOpen}
          onClose={() => {
            setRunDialogOpen(false);
            queryClient.invalidateQueries(['my-experience-results']);
          }}
          experienceId={experienceId}
          experienceName={experience?.experience_name}
          steps={[]}
        />
      )}

      {/* Error Snackbar */}
      <Snackbar
        open={errorSnackbar.open}
        autoHideDuration={6000}
        onClose={handleCloseErrorSnackbar}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert onClose={handleCloseErrorSnackbar} severity="error" sx={{ width: '100%' }}>
          {errorSnackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default ExperienceDetailPage;
