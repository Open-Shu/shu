import React, { useMemo } from 'react';
import { useQuery } from 'react-query';
import {
    Box,
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    Divider,
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
} from '@mui/material';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { format } from 'date-fns';
import { experiencesAPI, extractDataFromResponse, formatError } from '../services/api';
import StepStatusIcon from './StepStatusIcon';

export default function ExperienceRunDetailDialog({ open, onClose, runId }) {
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
            ...state
        })).sort((a, b) => {
            if (!a.started_at) return 1;
            if (!b.started_at) return -1;
            return new Date(a.started_at) - new Date(b.started_at);
        });
    }, [run]);



    if (!open) return null;

    return (
        <Dialog
            open={open}
            onClose={onClose}
            maxWidth="md"
            fullWidth
        >
            <DialogTitle>
                Run Details
                {run && run.started_at && !isNaN(new Date(run.started_at).getTime()) && (
                    <Typography variant="body2" color="text.secondary">
                        {format(new Date(run.started_at), 'MMMM d, yyyy HH:mm:ss')}
                    </Typography>
                )}
            </DialogTitle>
            <DialogContent dividers>
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
                    <Stack spacing={3}>
                        {/* Status Banner */}
                        <Paper variant="outlined" sx={{ p: 2, bgcolor: 'grey.50' }}>
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
                        <Box>
                            <Typography variant="subtitle2" gutterBottom>Steps Execution</Typography>
                            <Paper variant="outlined">
                                <List dense>
                                    {steps.length === 0 ? (
                                        <ListItem>
                                            <ListItemText secondary="No steps recorded" />
                                        </ListItem>
                                    ) : (
                                        steps.map((step) => (
                                            <ListItem key={step.step_key}>
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
                                                                // If we have output summary we could show it, but usually not in state.
                                                                // We rely on status.
                                                                `Duration: ${step.finished_at && step.started_at
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
                                            </ListItem>
                                        ))
                                    )}
                                </List>
                            </Paper>
                        </Box>

                        {/* LLM Result */}
                        <Box sx={{ minHeight: 200 }}>
                            <Typography variant="subtitle2" gutterBottom>
                                Result Content
                            </Typography>
                            <Paper
                                variant="outlined"
                                sx={{
                                    p: 2,
                                    bgcolor: 'grey.50',
                                    minHeight: 200,
                                    maxHeight: 400,
                                    overflowY: 'auto'
                                }}
                            >
                                {run.result_content ? (
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                        {run.result_content}
                                    </ReactMarkdown>
                                ) : (
                                    <Typography color="text.secondary" variant="body2" fontStyle="italic">
                                        No result content.
                                    </Typography>
                                )}
                            </Paper>
                        </Box>

                        {/* Metadata */}
                        {run.result_metadata && (
                            <Box>
                                <Typography variant="subtitle2" gutterBottom>Metadata</Typography>
                                <Paper variant="outlined" sx={{ p: 2, bgcolor: 'grey.50' }}>
                                    <pre style={{ margin: 0, fontSize: '0.8rem', overflowX: 'auto' }}>
                                        {JSON.stringify(run.result_metadata, null, 2)}
                                    </pre>
                                </Paper>
                            </Box>
                        )}
                    </Stack>
                )}
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>Close</Button>
            </DialogActions>
        </Dialog>
    );
}
