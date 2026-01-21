import React, { useState, useEffect, useRef } from 'react';
import {
    Box,
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    List,
    ListItem,
    ListItemIcon,
    ListItemText,
    Paper,
    Stack,
    Typography,
    CircularProgress,
    Alert,
    Collapse,
    IconButton,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Chip,
} from '@mui/material';
import {
    ExpandMore as ExpandMoreIcon,
    ExpandLess as ExpandLessIcon,
} from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import { experiencesAPI } from '../services/api';
import StepStatusIcon from './StepStatusIcon';
import MarkdownRenderer from './shared/MarkdownRenderer';

export default function ExperienceRunDialog({ open, onClose, experienceId, experienceName, steps = [] }) {
    const theme = useTheme();
    const isDarkMode = theme.palette.mode === 'dark';
    
    const [status, setStatus] = useState('pending'); // pending, running, completed, failed
    const [stepStates, setStepStates] = useState({}); // { step_key: { status, summary, error, data } }
    const [expandedSteps, setExpandedSteps] = useState({}); // { step_key: boolean }
    const [llmContent, setLlmContent] = useState('');
    const [error, setError] = useState(null);
    const abortControllerRef = useRef(null);

    const toggleStepExpanded = (stepKey) => {
        setExpandedSteps(prev => ({
            ...prev,
            [stepKey]: !prev[stepKey]
        }));
    };

    // Helper function to render data in a human-readable format
    const renderDataValue = (value) => {
        if (value === null || value === undefined) {
            return <Typography variant="body2" color="text.secondary" fontStyle="italic">null</Typography>;
        }
        
        if (typeof value === 'boolean') {
            return <Chip label={value ? 'true' : 'false'} size="small" color={value ? 'success' : 'default'} />;
        }
        
        if (typeof value === 'number') {
            return <Typography variant="body2">{value}</Typography>;
        }
        
        if (typeof value === 'string') {
            // Check if it's a date string
            if (value.match(/^\d{4}-\d{2}-\d{2}/) && !isNaN(Date.parse(value))) {
                return <Typography variant="body2">{new Date(value).toLocaleString()}</Typography>;
            }
            return <Typography variant="body2">{value}</Typography>;
        }
        
        if (Array.isArray(value)) {
            if (value.length === 0) {
                return <Typography variant="body2" color="text.secondary" fontStyle="italic">empty array</Typography>;
            }
            // For arrays of primitives, show as chips
            if (value.every(item => typeof item !== 'object')) {
                return (
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                        {value.map((item, idx) => (
                            <Chip key={idx} label={String(item)} size="small" variant="outlined" />
                        ))}
                    </Box>
                );
            }
            // For arrays of objects, show count
            return <Typography variant="body2" color="text.secondary">{value.length} items</Typography>;
        }
        
        if (typeof value === 'object') {
            return <Typography variant="body2" color="text.secondary">object</Typography>;
        }
        
        return <Typography variant="body2">{String(value)}</Typography>;
    };

    // Helper function to render nested data structure
    const renderDataStructure = (data, depth = 0) => {
        if (!data || typeof data !== 'object') {
            return renderDataValue(data);
        }

        if (Array.isArray(data)) {
            if (data.length === 0) {
                return <Typography variant="body2" color="text.secondary" fontStyle="italic">Empty array</Typography>;
            }
            
            // If array of objects with similar structure, render as table
            if (data.every(item => typeof item === 'object' && !Array.isArray(item))) {
                const allKeys = [...new Set(data.flatMap(item => Object.keys(item)))];
                
                return (
                    <TableContainer>
                        <Table size="small">
                            <TableHead>
                                <TableRow>
                                    {allKeys.map(key => (
                                        <TableCell key={key} sx={{ fontWeight: 600 }}>
                                            {key.replace(/_/g, ' ')}
                                        </TableCell>
                                    ))}
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {data.map((item, idx) => (
                                    <TableRow key={idx}>
                                        {allKeys.map(key => (
                                            <TableCell key={key}>
                                                {renderDataValue(item[key])}
                                            </TableCell>
                                        ))}
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </TableContainer>
                );
            }
            
            // Otherwise, render as list
            return (
                <Stack spacing={1}>
                    {data.map((item, idx) => (
                        <Box key={idx} sx={{ pl: 2, borderLeft: 2, borderColor: 'divider' }}>
                            {renderDataStructure(item, depth + 1)}
                        </Box>
                    ))}
                </Stack>
            );
        }

        // Render object as key-value pairs
        const entries = Object.entries(data);
        if (entries.length === 0) {
            return <Typography variant="body2" color="text.secondary" fontStyle="italic">Empty object</Typography>;
        }

        return (
            <Stack spacing={1.5}>
                {entries.map(([key, value]) => {
                    const isNested = value && typeof value === 'object';
                    
                    return (
                        <Box key={key}>
                            <Typography 
                                variant="caption" 
                                sx={{ 
                                    fontWeight: 600, 
                                    color: 'text.secondary',
                                    textTransform: 'uppercase',
                                    letterSpacing: 0.5,
                                    display: 'block',
                                    mb: 0.5
                                }}
                            >
                                {key.replace(/_/g, ' ')}
                            </Typography>
                            {isNested ? (
                                <Box sx={{ 
                                    pl: 2, 
                                    borderLeft: 2, 
                                    borderColor: 'divider',
                                    mt: 0.5
                                }}>
                                    {renderDataStructure(value, depth + 1)}
                                </Box>
                            ) : (
                                renderDataValue(value)
                            )}
                        </Box>
                    );
                })}
            </Stack>
        );
    };

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
                                            summary: event.summary,
                                            data: event.data // Store the actual data returned by the step
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
            setStepStates({});
            setExpandedSteps({});
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
            fullScreen
        >
            <DialogTitle>
                Execute: {experienceName}
                {status === 'running' && <Typography variant="caption" sx={{ ml: 2 }}>Running...</Typography>}
            </DialogTitle>
            <DialogContent dividers sx={{ height: 'calc(100vh - 120px)', display: 'flex', flexDirection: 'column' }}>
                <Stack spacing={3} sx={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                    {error && (
                        <Alert severity="error">{error}</Alert>
                    )}

                    {/* Steps Progress */}
                    <Box sx={{ flex: '0 0 auto', maxHeight: '40vh', display: 'flex', flexDirection: 'column' }}>
                        <Typography variant="subtitle2" gutterBottom>Steps Execution</Typography>
                        <Paper variant="outlined" sx={{ flex: 1, overflow: 'auto' }}>
                            <List dense>
                                {steps.map((step) => {
                                    const state = stepStates[step.step_key];
                                    const isExpanded = expandedSteps[step.step_key];
                                    const hasData = state?.data !== undefined && state?.data !== null;
                                    
                                    return (
                                        <React.Fragment key={step.step_key}>
                                            <ListItem>
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
                                                            {renderDataStructure(state.data)}
                                                        </Paper>
                                                    </Box>
                                                </Collapse>
                                            )}
                                        </React.Fragment>
                                    );
                                })}
                            </List>
                        </Paper>
                    </Box>

                    {/* LLM Output */}
                    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
                        <Typography variant="subtitle2" gutterBottom>
                            Synthesized Result
                        </Typography>
                        <Paper
                            variant="outlined"
                            sx={{
                                p: 2,
                                bgcolor: 'background.default',
                                flex: 1,
                                overflowY: 'auto',
                                display: 'flex',
                                flexDirection: 'column'
                            }}
                        >
                            {llmContent ? (
                                <MarkdownRenderer
                                    content={llmContent}
                                    isDarkMode={isDarkMode}
                                />
                            ) : (
                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                    {status === 'running' && (
                                        <CircularProgress size={16} />
                                    )}
                                    <Typography color="text.secondary" variant="body2" fontStyle="italic">
                                        {status === 'running' 
                                            ? (() => {
                                                // Check if all steps are complete (succeeded, failed, or skipped)
                                                const allStepsComplete = steps.length > 0 && steps.every(step => {
                                                    const state = stepStates[step.step_key];
                                                    return state && ['succeeded', 'failed', 'skipped'].includes(state.status);
                                                });
                                                
                                                return allStepsComplete 
                                                    ? 'Generating AI response...' 
                                                    : 'Executing workflow steps...';
                                            })()
                                            : 'No output generated.'
                                        }
                                    </Typography>
                                </Box>
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
