import React, { useMemo, useState } from 'react';
import { useQuery } from 'react-query';
import { useNavigate } from 'react-router-dom';
import {
    Box,
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    Grid,
    List,
    ListItem,
    ListItemIcon,
    ListItemText,
    Paper,
    Stack,
    Typography,
    CircularProgress,
    Alert,
    Chip,
    Collapse,
    IconButton,
    Accordion,
    AccordionSummary,
    AccordionDetails,
} from '@mui/material';
import { 
    Chat as ChatIcon,
    ExpandMore as ExpandMoreIcon,
    ExpandLess as ExpandLessIcon,
} from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import { format } from 'date-fns';
import { chatAPI, experiencesAPI, extractDataFromResponse, formatError } from '../services/api';
import StepStatusIcon from './StepStatusIcon';
import MarkdownRenderer from './shared/MarkdownRenderer';
import DataRenderer from './shared/DataRenderer';
import { formatDateTimeFull } from '../utils/timezoneFormatter';
import log from '../utils/log';

export default function ExperienceRunDetailDialog({ open, onClose, runId, timezone }) {
    const theme = useTheme();
    const navigate = useNavigate();
    const isDarkMode = theme.palette.mode === 'dark';
    
    // State for conversation creation
    const [isCreatingConversation, setIsCreatingConversation] = useState(false);
    const [conversationError, setConversationError] = useState(null);
    
    // State for expandable step outputs
    const [expandedSteps, setExpandedSteps] = useState({});
    
    const { data: run, isLoading, error } = useQuery(
        ['experience-run', runId],
        () => experiencesAPI.getRun(runId).then(extractDataFromResponse),
        {
            enabled: !!runId && open,
        }
    );

    const steps = useMemo(() => {
        if (!run?.step_states) return [];
        return Object.entries(run.step_states).map(([key, state]) => ({
            step_key: key,
            ...state,
            // Merge in the actual output data from step_outputs
            data: run.step_outputs?.[key]
        })).sort((a, b) => {
            if (!a.started_at) return 1;
            if (!b.started_at) return -1;
            return new Date(a.started_at) - new Date(b.started_at);
        });
    }, [run]);

    const toggleStepExpanded = (stepKey) => {
        setExpandedSteps(prev => ({
            ...prev,
            [stepKey]: !prev[stepKey]
        }));
    };

    const handleStartConversation = async () => {
        if (!runId || !run?.result_content) {
            setConversationError('No result content available to start conversation');
            return;
        }

        try {
            setIsCreatingConversation(true);
            setConversationError(null);
            
            // Create conversation from experience run
            const response = await chatAPI.createConversationFromExperience(runId);
            const conversation = extractDataFromResponse(response);
            
            log.info('Started conversation from experience run', { 
                conversationId: conversation.id, 
                runId: runId
            });
            
            // Navigate to chat with conversation ID as query parameter
            const targetUrl = `/chat?conversationId=${conversation.id}`;
            log.info('Navigating to conversation', { targetUrl, conversationId: conversation.id });
            
            // This ensures navigation happens before component unmounts
            navigate(targetUrl);
            onClose();
        } catch (error) {
            log.error('Failed to start conversation from experience:', error);
            setConversationError(formatError(error) || 'Failed to start conversation. Please try again.');
        } finally {
            setIsCreatingConversation(false);
        }
    };



    if (!open) return null;

    return (
        <Dialog
            open={open}
            onClose={onClose}
            maxWidth={false}
            PaperProps={{
                sx: {
                    width: '95vw',
                    height: '95vh',
                    maxWidth: '95vw',
                    maxHeight: '95vh',
                }
            }}
        >
            <DialogTitle>
                Run Details
                {run && run.started_at && !isNaN(new Date(run.started_at).getTime()) && (
                    <Typography variant="body2" color="text.secondary">
                        {timezone 
                            ? formatDateTimeFull(run.started_at, timezone)
                            : format(new Date(run.started_at), 'MMMM d, yyyy HH:mm:ss')
                        }
                    </Typography>
                )}
            </DialogTitle>
            <DialogContent dividers sx={{ height: 'calc(95vh - 120px)', display: 'flex', flexDirection: 'column' }}>
                {isLoading && (
                    <Box display="flex" justifyContent="center" p={4}>
                        <CircularProgress />
                    </Box>
                )}

                {error && (
                    <Alert severity="error">
                        {formatError(error)}
                    </Alert>
                )}

                {run && (
                    <Stack spacing={3} sx={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                        {/* Conversation Error Alert */}
                        {conversationError && (
                            <Alert severity="error" onClose={() => setConversationError(null)}>
                                {conversationError}
                            </Alert>
                        )}

                        {/* Status Banner */}
                        <Paper variant="outlined" sx={{ p: 2, bgcolor: 'grey.50', flex: '0 0 auto' }}>
                            <Grid container spacing={2} alignItems="center">
                                <Grid item xs={12} sm={6}>
                                    <Stack direction="row" spacing={1} alignItems="center">
                                        <Typography variant="body2" fontWeight="bold">Status:</Typography>
                                        <Chip
                                            label={run.status}
                                            color={run.status === 'succeeded' ? 'success' : run.status === 'failed' ? 'error' : 'default'}
                                            size="small"
                                        />
                                    </Stack>
                                </Grid>
                                <Grid item xs={12} sm={6}>
                                    <Typography variant="body2">
                                        <strong>User:</strong> {run.user?.email}
                                    </Typography>
                                </Grid>
                                {run.error_message && (
                                    <Grid item xs={12}>
                                        <Alert severity="error" sx={{ mt: 1 }}>
                                            {run.error_message}
                                        </Alert>
                                    </Grid>
                                )}
                            </Grid>
                        </Paper>

                        {/* Steps Timeline */}
                        <Box sx={{ flex: '0 0 auto', maxHeight: '25vh', display: 'flex', flexDirection: 'column' }}>
                            <Typography variant="subtitle2" gutterBottom>Steps Execution</Typography>
                            <Paper variant="outlined" sx={{ flex: 1, overflow: 'auto' }}>
                                <List dense>
                                    {steps.length === 0 ? (
                                        <ListItem>
                                            <ListItemText secondary="No steps recorded" />
                                        </ListItem>
                                    ) : (
                                        steps.map((step) => {
                                            const isExpanded = expandedSteps[step.step_key];
                                            const hasData = step.data !== undefined && step.data !== null;
                                            
                                            return (
                                                <React.Fragment key={step.step_key}>
                                                    <ListItem>
                                                        <ListItemIcon>
                                                            <StepStatusIcon state={step} />
                                                        </ListItemIcon>
                                                        <ListItemText
                                                            primary={step.step_key}
                                                            secondary={
                                                                <React.Fragment>
                                                                    {step.status === 'failed' && `Error: ${step.error || 'Unknown error'}`}
                                                                    {step.status === 'skipped' && `Skipped: ${step.reason || 'No reason provided'}`}
                                                                    {step.status === 'succeeded' && (
                                                                        step.summary || `Duration: ${step.finished_at && step.started_at
                                                                            ? ((new Date(step.finished_at) - new Date(step.started_at)) / 1000).toFixed(2) + 's'
                                                                            : '-'
                                                                        }`
                                                                    )}
                                                                </React.Fragment>
                                                            }
                                                            primaryTypographyProps={{
                                                                color: step.status === 'failed' ? 'error' : 'textPrimary'
                                                            }}
                                                        />
                                                        {hasData && (
                                                            <IconButton
                                                                size="small"
                                                                onClick={() => toggleStepExpanded(step.step_key)}
                                                                sx={{ ml: 1 }}
                                                            >
                                                                {isExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                                                            </IconButton>
                                                        )}
                                                    </ListItem>
                                                    {hasData && (
                                                        <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                                                            <Box sx={{ px: 2, pb: 2, pl: 9 }}>
                                                                <Paper
                                                                    variant="outlined"
                                                                    sx={{
                                                                        p: 2,
                                                                        bgcolor: 'background.default',
                                                                        maxHeight: '30vh',
                                                                        overflowY: 'auto',
                                                                    }}
                                                                >
                                                                    <Typography
                                                                        variant="caption"
                                                                        color="text.secondary"
                                                                        sx={{ display: 'block', mb: 2, fontWeight: 600 }}
                                                                    >
                                                                        Step Output Data:
                                                                    </Typography>
                                                                    <DataRenderer data={step.data} />
                                                                </Paper>
                                                            </Box>
                                                        </Collapse>
                                                    )}
                                                </React.Fragment>
                                            );
                                        })
                                    )}
                                </List>
                            </Paper>
                        </Box>

                        {/* LLM Result */}
                        <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
                            <Typography variant="subtitle2" gutterBottom>
                                Result Content
                            </Typography>
                            <Paper
                                variant="outlined"
                                sx={{
                                    p: 2,
                                    bgcolor: 'grey.50',
                                    flex: 1,
                                    overflowY: 'auto'
                                }}
                            >
                                {run.result_content ? (
                                    <MarkdownRenderer
                                        content={run.result_content}
                                        isDarkMode={isDarkMode}
                                    />
                                ) : (
                                    <Typography color="text.secondary" variant="body2" fontStyle="italic">
                                        No result content.
                                    </Typography>
                                )}
                            </Paper>
                        </Box>

                        {/* Metadata - Accordion */}
                        {run.result_metadata && (
                            <Accordion sx={{ flex: '0 0 auto' }}>
                                <AccordionSummary
                                    expandIcon={<ExpandMoreIcon />}
                                    aria-controls="metadata-content"
                                    id="metadata-header"
                                >
                                    <Typography variant="subtitle2">Metadata</Typography>
                                </AccordionSummary>
                                <AccordionDetails>
                                    <Paper 
                                        variant="outlined" 
                                        sx={{ 
                                            p: 2, 
                                            bgcolor: 'background.default',
                                            maxHeight: '30vh', 
                                            overflowY: 'auto' 
                                        }}
                                    >
                                        <DataRenderer data={run.result_metadata} />
                                    </Paper>
                                </AccordionDetails>
                            </Accordion>
                        )}
                    </Stack>
                )}
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>Close</Button>
                {run?.result_content && (
                    <Button
                        variant="contained"
                        startIcon={<ChatIcon />}
                        onClick={handleStartConversation}
                        disabled={isCreatingConversation}
                    >
                        {isCreatingConversation ? 'Starting...' : 'Start Conversation'}
                    </Button>
                )}
            </DialogActions>
        </Dialog>
    );
}
