import React, { useState, useEffect, useRef } from 'react';
import {
    Box,
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    Divider,
    List,
    ListItem,
    ListItemIcon,
    ListItemText,
    Paper,
    Stack,
    Typography,
    CircularProgress,
    Alert,
} from '@mui/material';
import {
    PlayArrow as RunIcon,
} from '@mui/icons-material';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { experiencesAPI } from '../services/api';
import StepStatusIcon from './StepStatusIcon';

export default function ExperienceRunDialog({ open, onClose, experienceId, experienceName, steps = [] }) {
    const [status, setStatus] = useState('pending'); // pending, running, completed, failed
    const [logs, setLogs] = useState([]); // List of parsed events for debugging
    const [stepStates, setStepStates] = useState({}); // { step_key: { status, summary, error } }
    const [llmContent, setLlmContent] = useState('');
    const [error, setError] = useState(null);
    const abortControllerRef = useRef(null);

    const startExecution = React.useCallback(async () => {
        try {
            abortControllerRef.current = new AbortController();

            const response = await experiencesAPI.streamRun(
                experienceId,
                { params: {} }, // Default empty params for "Run Now"
                { signal: abortControllerRef.current.signal }
            );

            if (!response.ok) {
                const text = await response.text();
                throw new Error(text || `HTTP error ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            try {
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    const chunk = decoder.decode(value, { stream: true });
                    buffer += chunk;
                    const parts = buffer.split('\n\n');
                    buffer = parts.pop() || ''; // Keep incomplete part

                    for (const part of parts) {
                        if (part.startsWith('data: ')) {
                            const jsonStr = part.slice(6);
                            if (jsonStr.trim() === '[DONE]') continue;

                            try {
                                const event = JSON.parse(jsonStr);
                                setLogs(prev => [...prev, event]);

                                // Process events for state updates
                                if (event.type === 'run_started') setStatus('running');
                                else if (event.type === 'step_started') {
                                    setStepStates(prev => ({
                                        ...prev,
                                        [event.step_key]: { status: 'running', ...prev[event.step_key] }
                                    }));
                                }
                                else if (event.type === 'step_completed') {
                                    setStepStates(prev => ({
                                        ...prev,
                                        [event.step_key]: {
                                            status: 'succeeded',
                                            summary: event.summary
                                        }
                                    }));
                                }
                                else if (event.type === 'step_failed') {
                                    setStepStates(prev => ({
                                        ...prev,
                                        [event.step_key]: {
                                            status: 'failed',
                                            error: event.error
                                        }
                                    }));
                                }
                                else if (event.type === 'step_skipped') {
                                    setStepStates(prev => ({
                                        ...prev,
                                        [event.step_key]: {
                                            status: 'skipped',
                                            reason: event.reason
                                        }
                                    }));
                                }
                                else if (event.type === 'content_delta') {
                                    setLlmContent(prev => prev + (event.content || ''));
                                }
                                else if (event.type === 'run_completed') setStatus('completed');
                                else if (event.type === 'error') {
                                    setError(event.message);
                                    setStatus('failed');
                                }
                            } catch (e) {
                                console.warn('Failed to parse SSE event:', e);
                            }
                        }
                    }
                }
            } finally {
                reader.cancel();
            }

        } catch (err) {
            if (err.name === 'AbortError') {
                console.log('Execution aborted');
            } else {
                console.error('Execution error:', err);
                setError(err.message || 'Failed to execute experience');
                setStatus('failed');
            }
        }
    }, [experienceId]);

    // Reset state when opening
    useEffect(() => {
        if (open) {
            setStatus('running');
            setLogs([]);
            setStepStates({});
            setLlmContent('');
            setError(null);

            startExecution();
        } else {
            // Cleanup on close
            if (abortControllerRef.current) {
                abortControllerRef.current.abort();
            }
        }
    }, [open, startExecution]);

    // Handle incoming events (helper function merged into startExecution loop)
    // const handleEvent = ... removed to avoid stale closures





    return (
        <Dialog
            open={open}
            onClose={(e, reason) => {
                // Prevent closing by clicking outside while running
                if (reason === 'backdropClick' && status === 'running') return;
                onClose();
            }}
            maxWidth="md"
            fullWidth
        >
            <DialogTitle>
                Execute: {experienceName}
                {status === 'running' && <Typography variant="caption" sx={{ ml: 2 }}>Running...</Typography>}
            </DialogTitle>
            <DialogContent dividers>
                <Stack spacing={3}>
                    {error && (
                        <Alert severity="error">{error}</Alert>
                    )}

                    {/* Steps Progress */}
                    <Box>
                        <Typography variant="subtitle2" gutterBottom>Steps Execution</Typography>
                        <Paper variant="outlined">
                            <List dense>
                                {steps.map((step) => {
                                    const state = stepStates[step.step_key];
                                    return (
                                        <ListItem key={step.step_key}>
                                            <ListItemIcon>
                                                <StepStatusIcon state={state} />
                                            </ListItemIcon>
                                            <ListItemText
                                                primary={step.step_key}
                                                secondary={
                                                    state?.error ? `Error: ${state.error}` :
                                                        state?.summary ? state.summary :
                                                            state?.reason ? `Skipped: ${state.reason}` :
                                                                step.step_type
                                                }
                                                primaryTypographyProps={{
                                                    color: state?.status === 'failed' ? 'error' : 'textPrimary'
                                                }}
                                            />
                                        </ListItem>
                                    );
                                })}
                            </List>
                        </Paper>
                    </Box>

                    {/* LLM Output */}
                    <Box sx={{ minHeight: 200 }}>
                        <Typography variant="subtitle2" gutterBottom>
                            Synthesized Result
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
                            {llmContent ? (
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                    {llmContent}
                                </ReactMarkdown>
                            ) : (
                                <Typography color="text.secondary" variant="body2" fontStyle="italic">
                                    {status === 'running' ? 'Waiting for steps to complete...' : 'No output generated.'}
                                </Typography>
                            )}
                        </Paper>
                    </Box>
                </Stack>
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose} disabled={status === 'running'}>
                    Close
                </Button>
            </DialogActions>
        </Dialog>
    );
}
