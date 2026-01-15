import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from 'react-query';
import {
    Alert,
    Box,
    CircularProgress,
    IconButton,
    Paper,
    Typography,
} from '@mui/material';
import { ArrowBack as ArrowBackIcon } from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import { experiencesAPI, extractDataFromResponse, formatError } from '../services/api';
import MarkdownRenderer from '../components/shared/MarkdownRenderer';
import { formatDateTimeFull } from '../utils/timezoneFormatter';

/**
 * Full-width page displaying a single experience result.
 * Accessible via /dashboard/experience/:experienceId route.
 */
const ExperienceDetailPage = () => {
    const { experienceId } = useParams();
    const navigate = useNavigate();
    const theme = useTheme();

    // Detect dark mode from theme
    const isDarkMode = theme.palette.mode === 'dark';

    // Fetch experience details from my-results endpoint
    const {
        data: results,
        isLoading,
        error,
    } = useQuery(
        ['my-experience-results'],
        () => experiencesAPI.getMyResults().then(extractDataFromResponse),
        {
            staleTime: 30000,
        }
    );

    const handleBack = () => {
        navigate('/dashboard');
    };

    // Find the specific experience from results
    const experiences = results?.experiences || [];
    const experience = experiences.find((exp) => exp.experience_id === experienceId);

    return (
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
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
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                    {experience?.experience_name || 'Experience Details'}
                </Typography>
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
                    <Alert severity="warning">
                        Experience not found or no results available.
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
                                        : new Date(experience.latest_run_finished_at).toLocaleString()
                                    }
                                </Typography>
                            </Box>
                        )}
                        
                        <MarkdownRenderer
                            content={experience.result_preview}
                            isDarkMode={isDarkMode}
                        />
                    </Box>
                )}
            </Box>
        </Box>
    );
};

export default ExperienceDetailPage;
