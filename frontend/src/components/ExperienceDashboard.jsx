import React, { useState } from 'react';
import { useQuery } from 'react-query';
import {
    Alert,
    Box,
    Button,
    Card,
    CardContent,
    CircularProgress,
    IconButton,
    Stack,
    Typography,
} from '@mui/material';
import {
    Add as AddIcon,
    Refresh as RefreshIcon,
    ExpandMore as ExpandIcon,
    ExpandLess as CollapseIcon,
    SmartToy as BotIcon,
} from '@mui/icons-material';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { format } from 'date-fns';
import { experiencesAPI, extractDataFromResponse, formatError } from '../services/api';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import { getBrandingAppName } from '../utils/constants';



const ExperienceResultCard = ({ experience, onStartChat }) => {
    const [expanded, setExpanded] = useState(false);
    const hasResult = !!experience.latest_run_id;

    // Show first ~500 chars as preview, full on expand
    const previewText = experience.result_preview || '';
    const isLong = previewText.length > 500;
    const displayText = expanded ? previewText : (isLong ? previewText.slice(0, 500) + '...' : previewText);

    return (
        <Card>
            <CardContent>
                {/* Header: Icon + Title + Run Date */}
                <Box display="flex" alignItems="center" mb={2}>
                    <BotIcon color="primary" sx={{ mr: 1 }} />
                    <Typography variant="h6" sx={{ flex: 1 }}>
                        {experience.experience_name}
                    </Typography>
                    {experience.latest_run_finished_at && (
                        <Typography variant="caption" color="text.secondary">
                            {format(new Date(experience.latest_run_finished_at), 'MMM d, yyyy h:mm a')}
                        </Typography>
                    )}
                </Box>



                {/* Result Content - visible by default */}
                {hasResult && previewText ? (
                    <Box
                        sx={{
                            p: 2,
                            bgcolor: 'grey.50',
                            borderRadius: 1,
                            maxHeight: expanded ? 600 : 200,
                            overflowY: 'auto',
                            mb: 2,
                        }}
                    >
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {displayText}
                        </ReactMarkdown>
                    </Box>
                ) : (
                    <Typography variant="body2" color="text.secondary" fontStyle="italic" sx={{ mb: 2 }}>
                        {hasResult ? 'No result content' : 'Not run yet'}
                    </Typography>
                )}

                {/* Footer: Actions */}
                <Box display="flex" alignItems="center" justifyContent="flex-end">
                    <Stack direction="row" spacing={1}>
                        {isLong && (
                            <Button
                                size="small"
                                variant="text"
                                onClick={() => setExpanded(!expanded)}
                                endIcon={expanded ? <CollapseIcon /> : <ExpandIcon />}
                            >
                                {expanded ? 'Show less' : 'Show more'}
                            </Button>
                        )}
                        {hasResult && experience.latest_run_status === 'succeeded' && (
                            <Button
                                size="small"
                                variant="contained"
                                startIcon={<AddIcon />}
                                onClick={() => onStartChat(experience)}
                            >
                                Start Chat
                            </Button>
                        )}
                    </Stack>
                </Box>

                {/* Missing Identities Warning */}
                {!experience.can_run && experience.missing_identities?.length > 0 && (
                    <Alert severity="warning" sx={{ mt: 2 }}>
                        Missing required connections: {experience.missing_identities.join(', ')}
                    </Alert>
                )}
            </CardContent>
        </Card>
    );
};

export default function ExperienceDashboard({
    onStartChat,
    onCreateConversation,
    createConversationDisabled,
}) {
    const { branding } = useAppTheme();
    const appDisplayName = getBrandingAppName(branding);

    // Fetch user's experience results
    const {
        data: results,
        isLoading,
        error,
        refetch,
    } = useQuery(
        ['my-experience-results'],
        () => experiencesAPI.getMyResults().then(extractDataFromResponse),
        {
            staleTime: 30000,
            refetchInterval: 60000, // Auto-refresh every minute
        }
    );



    const handleStartChat = (experience) => {
        if (onStartChat) {
            onStartChat(experience);
        }
    };

    const experiences = results?.experiences || [];

    return (
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'auto' }}>
            {/* Header */}
            <Box sx={{ p: { xs: 2, sm: 3 }, pb: 0 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                        <BotIcon sx={{ fontSize: 40, color: 'primary.main' }} />
                        <Box>
                            <Typography variant="h5" sx={{ fontWeight: 600 }}>
                                {appDisplayName}
                            </Typography>
                            <Typography variant="body2" color="text.secondary">
                                Your personalized experiences and results
                            </Typography>
                        </Box>
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

            {/* Content */}
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
                )}

                {!isLoading && !error && experiences.length > 0 && (
                    <Stack spacing={2}>
                        {experiences.map((exp) => (
                            <ExperienceResultCard
                                key={exp.experience_id}
                                experience={exp}
                                onStartChat={handleStartChat}
                            />
                        ))}
                    </Stack>
                )}
            </Box>


        </Box>
    );
}
