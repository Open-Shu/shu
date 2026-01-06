import React from 'react';
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
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { experiencesAPI, extractDataFromResponse, formatError } from '../services/api';

/**
 * Full-width page displaying a single experience result.
 * Accessible via /dashboard/experience/:experienceId route.
 */
const ExperienceDetailPage = () => {
    const { experienceId } = useParams();
    const navigate = useNavigate();

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
                            '& img': { maxWidth: '100%' },
                            '& pre': {
                                overflow: 'auto',
                                bgcolor: 'grey.100',
                                p: 2,
                                borderRadius: 1,
                            },
                            '& code': {
                                bgcolor: 'grey.100',
                                px: 0.5,
                                borderRadius: 0.5,
                            },
                        }}
                    >
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {experience.result_preview || 'No content available.'}
                        </ReactMarkdown>
                    </Box>
                )}
            </Box>
        </Box>
    );
};

export default ExperienceDetailPage;
