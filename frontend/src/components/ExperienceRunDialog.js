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
} from '@mui/material';
import { ExpandMore as ExpandMoreIcon, ExpandLess as ExpandLessIcon } from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import { experiencesAPI } from '../services/api';
import StepStatusIcon from './StepStatusIcon';
import MarkdownRenderer from './shared/MarkdownRenderer';
import DataRenderer from './shared/DataRenderer';
import log from '../utils/log';

export default function ExperienceRunDialog({ open, onClose, experienceId, experienceName, steps = [] }) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';

  const [status, setStatus] = useState('pending'); // pending, running, completed, failed
  const [stepStates, setStepStates] = useState({}); // { step_key: { status, summary, error, data } }
  const [expandedSteps, setExpandedSteps] = useState({}); // { step_key: boolean }
  const [discoveredStepKeys, setDiscoveredStepKeys] = useState([]); // step keys discovered from SSE events
  const [llmContent, setLlmContent] = useState('');
  const [error, setError] = useState(null);
  const abortControllerRef = useRef(null);
  const executionStartedRef = useRef(false); // Track if execution has started

  // Use provided steps if available, otherwise use dynamically discovered steps from SSE events
  const displaySteps =
    steps.length > 0
      ? steps
      : discoveredStepKeys.map((key) => ({ step_key: key, step_type: stepStates[key]?.step_type || 'plugin' }));

  const toggleStepExpanded = (stepKey) => {
    setExpandedSteps((prev) => ({
      ...prev,
      [stepKey]: !prev[stepKey],
    }));
  };

  const startExecution = React.useCallback(async () => {
    log.debug('[ExperienceRunDialog] startExecution called for experience:', experienceId);
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
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { value, done } = await reader.read();
          if (done) {
            break;
          }

          const chunk = decoder.decode(value, { stream: true });
          buffer += chunk;
          const parts = buffer.split('\n\n');
          buffer = parts.pop() || ''; // Keep incomplete part

          for (const part of parts) {
            if (part.startsWith('data: ')) {
              const jsonStr = part.slice(6);
              if (jsonStr.trim() === '[DONE]') {
                continue;
              }
              try {
                const event = JSON.parse(jsonStr);

                // Process events for state updates
                if (event.type === 'run_started') {
                  setStatus('running');
                } else if (event.type === 'step_started') {
                  setDiscoveredStepKeys((prev) => (prev.includes(event.step_key) ? prev : [...prev, event.step_key]));
                  setStepStates((prev) => ({
                    ...prev,
                    [event.step_key]: { ...prev[event.step_key], status: 'running', step_type: event.step_type },
                  }));
                } else if (event.type === 'step_completed') {
                  setStepStates((prev) => ({
                    ...prev,
                    [event.step_key]: {
                      status: 'succeeded',
                      summary: event.summary,
                      data: event.data, // Store the actual data returned by the step
                    },
                  }));
                } else if (event.type === 'step_failed') {
                  setStepStates((prev) => ({
                    ...prev,
                    [event.step_key]: {
                      status: 'failed',
                      error: event.error,
                    },
                  }));
                } else if (event.type === 'step_skipped') {
                  setStepStates((prev) => ({
                    ...prev,
                    [event.step_key]: {
                      status: 'skipped',
                      reason: event.reason,
                    },
                  }));
                } else if (event.type === 'content_delta') {
                  setLlmContent((prev) => prev + (event.content || ''));
                } else if (event.type === 'run_completed') {
                  setStatus('completed');
                } else if (event.type === 'error') {
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
    log.debug(
      '[ExperienceRunDialog] useEffect triggered - open:',
      open,
      'experienceId:',
      experienceId,
      'executionStarted:',
      executionStartedRef.current
    );
    if (open && !executionStartedRef.current) {
      executionStartedRef.current = true;
      setStatus('running');
      setStepStates({});
      setExpandedSteps({});
      setDiscoveredStepKeys([]);
      setLlmContent('');
      setError(null);

      log.debug('[ExperienceRunDialog] Starting execution for experience:', experienceId);
      startExecution();
    } else if (!open) {
      // Cleanup on close
      executionStartedRef.current = false;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]); // Only depend on 'open', not 'startExecution' to avoid double execution

  // Handle incoming events (helper function merged into startExecution loop)
  // const handleEvent = ... removed to avoid stale closures

  return (
    <Dialog
      open={open}
      onClose={(e, reason) => {
        // Prevent closing by clicking outside while running
        if (reason === 'backdropClick' && status === 'running') {
          return;
        }
        onClose();
      }}
      maxWidth={false}
      PaperProps={{
        sx: {
          width: '95vw',
          height: '95vh',
          maxWidth: '95vw',
          maxHeight: '95vh',
        },
      }}
    >
      <DialogTitle>
        Execute: {experienceName}
        {status === 'running' && (
          <Typography variant="caption" sx={{ ml: 2 }}>
            Running...
          </Typography>
        )}
      </DialogTitle>
      <DialogContent dividers sx={{ height: 'calc(95vh - 120px)', display: 'flex', flexDirection: 'column' }}>
        <Stack spacing={3} sx={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {error && <Alert severity="error">{error}</Alert>}

          {/* Steps Progress */}
          <Box sx={{ flex: '0 0 auto', maxHeight: '40vh', display: 'flex', flexDirection: 'column' }}>
            <Typography variant="subtitle2" gutterBottom>
              Steps Execution
            </Typography>
            <Paper variant="outlined" sx={{ flex: 1, overflow: 'auto' }}>
              <List dense>
                {displaySteps.map((step) => {
                  const state = stepStates[step.step_key];
                  const isExpanded = expandedSteps[step.step_key];
                  const hasData = steps.length > 0 && state?.data !== undefined && state?.data !== null;

                  return (
                    <React.Fragment key={step.step_key}>
                      <ListItem>
                        <ListItemIcon>
                          <StepStatusIcon state={state} />
                        </ListItemIcon>
                        <ListItemText
                          primary={step.step_key}
                          secondary={
                            state?.error
                              ? `Error: ${state.error}`
                              : state?.summary
                                ? state.summary
                                : state?.reason
                                  ? `Skipped: ${state.reason}`
                                  : step.step_type
                          }
                          primaryTypographyProps={{
                            color: state?.status === 'failed' ? 'error' : 'textPrimary',
                          }}
                        />
                        {hasData && (
                          <IconButton
                            size="small"
                            onClick={() => toggleStepExpanded(step.step_key)}
                            sx={{ ml: 1 }}
                            aria-label={isExpanded ? `Collapse step ${step.step_key}` : `Expand step ${step.step_key}`}
                            aria-expanded={isExpanded}
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
                              <DataRenderer data={state.data} />
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
                flexDirection: 'column',
              }}
            >
              {llmContent ? (
                <MarkdownRenderer
                  key={status === 'completed' ? 'final' : 'streaming'}
                  content={llmContent}
                  isDarkMode={isDarkMode}
                />
              ) : (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  {status === 'running' && <CircularProgress size={16} />}
                  <Typography color="text.secondary" variant="body2" fontStyle="italic">
                    {status === 'running'
                      ? (() => {
                          // Check if all steps are complete (succeeded, failed, or skipped)
                          const allStepsComplete =
                            displaySteps.length > 0 &&
                            displaySteps.every((step) => {
                              const state = stepStates[step.step_key];
                              return state && ['succeeded', 'failed', 'skipped'].includes(state.status);
                            });

                          return allStepsComplete ? 'Generating AI response...' : 'Executing workflow steps...';
                        })()
                      : 'No output generated.'}
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
