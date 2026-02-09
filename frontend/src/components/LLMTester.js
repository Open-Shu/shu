import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation } from 'react-query';
import {
  Box,
  Typography,
  TextField,
  Button,
  Card,
  CardContent,
  Grid,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Alert,
  CircularProgress,
  Tabs,
  Tab,
  Paper,
  IconButton,
  Divider,
  Chip,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Switch,
  FormControlLabel,
  LinearProgress,
} from '@mui/material';
import {
  PlayArrow as TestIcon,
  ContentCopy as CopyIcon,
  ExpandMore as ExpandMoreIcon,
  SmartToy as ModelIcon,
  Psychology as PromptIcon,
  Storage as KnowledgeBaseIcon,
  Settings as SettingsIcon,
  Timer as TimerIcon,
  Error as ErrorIcon,
  AttachFile as AttachFileIcon,
  Close as CloseIcon,
  Image as ImageIcon,
} from '@mui/icons-material';
import {
  llmAPI,
  knowledgeBaseAPI,
  queryAPI,
  modelConfigAPI,
  formatError,
  extractDataFromResponse,
  extractItemsFromResponse,
} from '../services/api';
import QueryConfiguration from './QueryConfiguration';
import SourcePreview from './SourcePreview';
import PageHelpHeader from './PageHelpHeader';
import JSONPretty from 'react-json-pretty';
import 'react-json-pretty/themes/monikai.css';

import log from '../utils/log';
function LLMTester({ prePopulatedConfigId = null, onTestStatusChange = null }) {
  // Component state
  const [selectedConfigId, setSelectedConfigId] = useState(prePopulatedConfigId || '');
  const [userMessage, setUserMessage] = useState('');
  const [enablePostProcessing, setEnablePostProcessing] = useState(true);
  const [streamState, setStreamState] = useState({
    isLoading: false,
    error: null,
    data: null,
  });
  const [activeTab, setActiveTab] = useState(0);

  // Timing state for test duration tracking
  const [testDuration, setTestDuration] = useState(null);

  // Attachment state for visual testing
  const [selectedFile, setSelectedFile] = useState(null);
  const fileInputRef = useRef(null);

  // Query configuration state (for knowledge base search)
  const [searchType, setSearchType] = useState('hybrid');
  const [searchLimit, setSearchLimit] = useState(10);
  const [searchThreshold, setSearchThreshold] = useState(null);
  const [titleWeightingEnabled, setTitleWeightingEnabled] = useState(true);
  const [titleWeightMultiplier, setTitleWeightMultiplier] = useState(3.0);

  // Fetch model configurations (including inactive for verification workflow)
  const {
    data: configurationsResponse,
    isLoading: configurationsLoading,
    refetch: refetchConfigurations,
  } = useQuery(['model-configurations', { includeInactive: true }], () =>
    modelConfigAPI.list({ include_relationships: true, active_only: false })
  );
  const configurations = extractItemsFromResponse(configurationsResponse);

  // Get selected configuration details
  const selectedConfig = configurations.find((c) => c.id === selectedConfigId);

  // Pre-populate configuration if provided - also handle prop changes
  useEffect(() => {
    if (prePopulatedConfigId) {
      // Refetch configurations to ensure we have the latest data (including inactive)
      refetchConfigurations();
    }
  }, [prePopulatedConfigId, refetchConfigurations]);

  // Set selectedConfigId when configurations are loaded and we have a prePopulatedConfigId
  // This ensures we wait for the configurations to be available before selecting
  useEffect(() => {
    if (prePopulatedConfigId && configurations.length > 0) {
      // Check if the config exists in the loaded configurations
      const configExists = configurations.some((c) => c.id === prePopulatedConfigId);
      if (configExists) {
        setSelectedConfigId(prePopulatedConfigId);
      }
    }
  }, [prePopulatedConfigId, configurations]);

  // Fetch LLM providers (for display purposes only)
  const { data: providersResponse } = useQuery('llm-providers', llmAPI.getProviders);
  const providers = extractItemsFromResponse(providersResponse);

  // Fetch knowledge bases (for display purposes only)
  const { data: knowledgeBasesResponse, isLoading: kbLoading } = useQuery('knowledge-bases', knowledgeBaseAPI.list);
  const knowledgeBases = extractItemsFromResponse(knowledgeBasesResponse);

  // Query mutation for fetching sources (separate from LLM completion)
  const sourcesMutation = useMutation(
    (params) => {
      const basePayload = {
        query: params.query,
        limit: params.limit,
        rag_rewrite_mode: 'raw_query',
      };

      if (params.searchType === 'similarity') {
        return queryAPI.search(params.kbId, {
          ...basePayload,
          query_type: 'similarity',
          similarity_threshold: params.threshold,
        });
      }

      if (params.searchType === 'keyword') {
        return queryAPI.search(params.kbId, {
          ...basePayload,
          query_type: 'keyword',
          similarity_threshold: params.threshold,
          title_weighting_enabled: params.titleWeightingEnabled,
          title_weight_multiplier: params.titleWeightingEnabled ? params.titleWeightMultiplier : 1.0,
        });
      }

      return queryAPI.search(params.kbId, {
        ...basePayload,
        query_type: 'hybrid',
        similarity_threshold: params.threshold,
        title_weighting_enabled: params.titleWeightingEnabled,
        title_weight_multiplier: params.titleWeightingEnabled ? params.titleWeightMultiplier : 1.0,
      });
    },
    {
      onError: (error) => {
        log.error('Sources query error:', error);
      },
    }
  );

  /**
   * Handle file upload for visual testing.
   */
  const handleUploadClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  /**
   * Handle file selection.
   */
  const handleFileSelected = useCallback((event) => {
    const file = event.target.files?.[0];
    if (file) {
      setSelectedFile(file);
    }
    event.target.value = ''; // Reset input
  }, []);

  /**
   * Remove the selected file.
   */
  const removeSelectedFile = useCallback(() => {
    setSelectedFile(null);
  }, []);

  /**
   * Handle LLM test using the dedicated test endpoint.
   * This uses non-streaming mode for better error messages from providers.
   */
  const handleTest = async () => {
    if (!selectedConfigId || !userMessage.trim()) {
      return;
    }

    // Fetch sources if KB is selected (regardless of post-processing)
    const hasKnowledgeBases = selectedConfig?.knowledge_bases?.length > 0;
    if (hasKnowledgeBases) {
      const firstKbId = selectedConfig.knowledge_bases[0].id;
      sourcesMutation.mutate({
        kbId: firstKbId,
        query: userMessage,
        searchType,
        limit: parseInt(searchLimit),
        threshold: parseFloat(searchThreshold),
        titleWeightingEnabled: titleWeightingEnabled,
        titleWeightMultiplier: titleWeightMultiplier,
      });
    }

    // Use the dedicated test endpoint (non-streaming for better error messages)
    // Capture start time in local variable to avoid stale state
    const localStartTime = Date.now();
    setStreamState({ isLoading: true, error: null, data: null });
    setTestDuration(null);

    try {
      // Create FormData to send file with test request
      const formData = new FormData();
      formData.append('test_message', userMessage);
      formData.append('include_knowledge_bases', enablePostProcessing.toString());

      if (selectedFile) {
        formData.append('file', selectedFile);
      }

      const response = await modelConfigAPI.testWithFile(selectedConfigId, formData);

      const endTime = Date.now();
      const duration = endTime - localStartTime;
      setTestDuration(duration);

      const result = extractDataFromResponse(response);

      if (result.success) {
        setStreamState({
          isLoading: false,
          error: null,
          data: {
            content: result.response,
            model: result.model_used,
            provider: providers.find((p) => p.id === selectedConfig?.llm_provider_id)?.name,
            usage: result.token_usage,
            post_processing_applied: result.prompt_applied,
            source_metadata: [],
            raw_content: null,
            response_time_ms: result.response_time_ms || duration,
          },
        });

        // Notify parent that test succeeded
        if (onTestStatusChange) {
          onTestStatusChange(true);
        }

        // Clear the selected file after successful test
        setSelectedFile(null);
      } else {
        // Test returned an error from the provider
        const errorMessage = result.error || 'Test failed';
        const isTimeout =
          errorMessage.toLowerCase().includes('timeout') || errorMessage.toLowerCase().includes('timed out');

        setStreamState({
          isLoading: false,
          error: {
            message: errorMessage,
            isTimeout: isTimeout,
            duration: duration,
          },
          data: null,
        });
      }
    } catch (err) {
      const endTime = Date.now();
      const duration = endTime - localStartTime;
      setTestDuration(duration);

      // Check if this is a timeout error
      const errorMessage = err?.message || 'Unknown error';
      const isTimeout =
        errorMessage.toLowerCase().includes('timeout') ||
        errorMessage.toLowerCase().includes('timed out') ||
        err?.code === 'ECONNABORTED';

      // Display error without crashing component
      // Preserve the original error structure for formatError compatibility
      log.error('LLM test error:', err);

      // Create an enhanced error object that preserves original error properties
      const enhancedError = Object.assign({}, err, {
        message: errorMessage,
        isTimeout: isTimeout,
        duration: duration,
      });

      setStreamState({
        isLoading: false,
        error: enhancedError,
        data: null,
      });
    }
  };

  /**
   * Format duration in milliseconds to a human-readable string.
   * @param {number} ms - Duration in milliseconds
   * @returns {string} Formatted duration string
   */
  const formatDuration = (ms) => {
    if (ms === null || ms === undefined) {
      return 'N/A';
    }
    if (ms < 1000) {
      return `${ms}ms`;
    }
    if (ms < 60000) {
      return `${(ms / 1000).toFixed(2)}s`;
    }
    const minutes = Math.floor(ms / 60000);
    const seconds = ((ms % 60000) / 1000).toFixed(1);
    return `${minutes}m ${seconds}s`;
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
  };

  const formatLLMRequest = () => {
    if (!selectedConfig) {
      return {
        provider: 'N/A',
        model: 'N/A',
        messages: [],
      };
    }

    const messages = [];

    // Add model prompt if configured
    if (selectedConfig.prompt) {
      messages.push({
        role: 'system',
        content: selectedConfig.prompt.content,
        source: 'Model Prompt',
      });
    }

    // Add KB prompts if configured
    if (selectedConfig.kb_prompt_assignments?.length > 0) {
      selectedConfig.kb_prompt_assignments.forEach((assignment) => {
        if (assignment.prompt) {
          messages.push({
            role: 'system',
            content: `${assignment.prompt.content}\n\n[KB Context would be inserted here]`,
            source: `KB Prompt (${assignment.knowledge_base?.name || 'KB'})`,
          });
        }
      });
    }

    messages.push({
      role: 'user',
      content: userMessage,
      source: 'User Input',
    });

    return {
      provider: providers.find((p) => p.id === selectedConfig.llm_provider_id)?.name || 'Unknown',
      model: selectedConfig.model_name,
      messages: messages,
    };
  };

  const llmResult = streamState.data || null;

  return (
    <Box sx={{ p: 3 }}>
      <PageHelpHeader
        title="LLM Tester"
        description="Test LLM calls directly using existing model configurations. Use this to verify model behavior, debug prompts, and experiment with configurations before using them in production."
        icon={<PromptIcon />}
        tips={[
          'Select a model configuration from the dropdown to test',
          'Type a message to test the configuration with your prompt',
          'Upload images to test vision capabilities (requires vision-enabled models)',
          'View configuration details to see provider, model, prompts, and knowledge bases',
          'View the Request Preview tab to see exactly what will be sent to the LLM',
          'This creates a temporary conversation for testing—results are cleaned up automatically',
        ]}
      />

      <Grid container spacing={3}>
        {/* Configuration Panel */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                <SettingsIcon sx={{ mr: 1, verticalAlign: 'middle' }} />
                Model Configuration
              </Typography>

              {/* Model Configuration Selection */}
              <FormControl fullWidth margin="normal" variant="outlined">
                <InputLabel id="config-select-label">Model Configuration</InputLabel>
                <Select
                  labelId="config-select-label"
                  value={selectedConfigId}
                  onChange={(e) => setSelectedConfigId(e.target.value)}
                  disabled={configurationsLoading || !!prePopulatedConfigId}
                >
                  {configurations.map((config) => (
                    <MenuItem key={config.id} value={config.id}>
                      {config.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              {/* Configuration Details */}
              {selectedConfig && (
                <Box sx={{ mt: 2 }}>
                  <Divider sx={{ my: 2 }}>
                    <Typography variant="caption" color="text.secondary">
                      Configuration Details
                    </Typography>
                  </Divider>

                  {/* Provider and Model */}
                  <Paper sx={{ p: 2, mb: 2, backgroundColor: 'grey.50' }}>
                    <Typography variant="subtitle2" gutterBottom>
                      <ModelIcon
                        sx={{
                          mr: 1,
                          verticalAlign: 'middle',
                          fontSize: '1rem',
                        }}
                      />
                      Provider & Model
                    </Typography>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mt: 1 }}>
                      <Chip
                        label={`Provider: ${providers.find((p) => p.id === selectedConfig.llm_provider_id)?.name || 'Unknown'}`}
                        size="small"
                        color="primary"
                      />
                      <Chip label={`Model: ${selectedConfig.model_name}`} size="small" color="primary" />
                    </Box>
                  </Paper>

                  {/* Model Prompt */}
                  {selectedConfig.prompt && (
                    <Paper sx={{ p: 2, mb: 2, backgroundColor: 'grey.50' }}>
                      <Typography variant="subtitle2" gutterBottom>
                        <PromptIcon
                          sx={{
                            mr: 1,
                            verticalAlign: 'middle',
                            fontSize: '1rem',
                          }}
                        />
                        Model Prompt
                      </Typography>
                      <Chip label={selectedConfig.prompt.name} size="small" color="secondary" />
                    </Paper>
                  )}

                  {/* Knowledge Bases */}
                  {selectedConfig.knowledge_bases?.length > 0 && (
                    <Paper sx={{ p: 2, mb: 2, backgroundColor: 'grey.50' }}>
                      <Typography variant="subtitle2" gutterBottom>
                        <KnowledgeBaseIcon
                          sx={{
                            mr: 1,
                            verticalAlign: 'middle',
                            fontSize: '1rem',
                          }}
                        />
                        Knowledge Bases
                      </Typography>
                      <Box
                        sx={{
                          display: 'flex',
                          flexWrap: 'wrap',
                          gap: 1,
                          mt: 1,
                        }}
                      >
                        {selectedConfig.knowledge_bases?.map((kb) => (
                          <Chip key={kb.id} label={kb.name} size="small" color="info" />
                        ))}
                      </Box>
                      {selectedConfig.kb_prompt_assignments?.length > 0 && (
                        <Box sx={{ mt: 1 }}>
                          <Typography variant="caption" color="text.secondary">
                            KB Prompts:
                          </Typography>
                          <Box
                            sx={{
                              display: 'flex',
                              flexWrap: 'wrap',
                              gap: 1,
                              mt: 0.5,
                            }}
                          >
                            {selectedConfig.kb_prompt_assignments.map((assignment) => (
                              <Chip
                                key={assignment.id}
                                label={assignment.prompt?.name || 'Unknown'}
                                size="small"
                                variant="outlined"
                                color="info"
                              />
                            ))}
                          </Box>
                        </Box>
                      )}
                    </Paper>
                  )}

                  {/* Query Configuration - only show when KB is selected */}
                  {selectedConfig.knowledge_bases?.length > 0 && (
                    <Box sx={{ mt: 2 }}>
                      <Divider sx={{ mb: 2 }}>
                        <Typography variant="caption" color="text.secondary">
                          Search Configuration
                        </Typography>
                      </Divider>

                      <QueryConfiguration
                        selectedKB={selectedConfig.knowledge_bases[0].id}
                        onKBChange={() => {}} // Read-only
                        queryText={userMessage}
                        onQueryTextChange={setUserMessage}
                        searchType={searchType}
                        onSearchTypeChange={setSearchType}
                        limit={searchLimit}
                        onLimitChange={setSearchLimit}
                        threshold={searchThreshold}
                        onThresholdChange={setSearchThreshold}
                        titleWeightingEnabled={titleWeightingEnabled}
                        onTitleWeightingEnabledChange={setTitleWeightingEnabled}
                        titleWeightMultiplier={titleWeightMultiplier}
                        onTitleWeightMultiplierChange={setTitleWeightMultiplier}
                        // UI customization - hide what we don't need in LLM Tester
                        showKBSelector={false} // Already have KB in config
                        showQueryText={false} // Will use the main user message field
                        queryTextLabel="Search Query"
                        queryTextPlaceholder="This will use the user message above for search..."
                        queryTextRows={2}
                        // Pass through data
                        kbLoading={kbLoading}
                        knowledgeBases={knowledgeBases}
                      />
                    </Box>
                  )}
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Test Panel */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Test Configuration
              </Typography>

              {/* User Message Input */}
              <TextField
                fullWidth
                multiline
                rows={4}
                label="User Message"
                value={userMessage}
                onChange={(e) => setUserMessage(e.target.value)}
                margin="normal"
                placeholder="Enter your test message here..."
              />

              {/* Attachment Upload Section */}
              <Box sx={{ mt: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  <ImageIcon sx={{ mr: 1, verticalAlign: 'middle', fontSize: '1rem' }} />
                  Visual Testing (Images)
                </Typography>

                {/* Hidden file input */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  style={{ display: 'none' }}
                  onChange={handleFileSelected}
                />

                {/* Upload button */}
                <Button
                  variant="outlined"
                  startIcon={<AttachFileIcon />}
                  onClick={handleUploadClick}
                  disabled={!selectedConfigId || streamState.isLoading}
                  size="small"
                  sx={{ mb: 1 }}
                >
                  {selectedFile ? 'Change Image' : 'Upload Image'}
                </Button>

                {/* Selected file */}
                {selectedFile && (
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mt: 1 }}>
                    <Chip
                      label={selectedFile.name}
                      onDelete={removeSelectedFile}
                      deleteIcon={<CloseIcon />}
                      color="primary"
                      variant="outlined"
                      size="small"
                      icon={<ImageIcon />}
                    />
                  </Box>
                )}

                {/* Vision capability warning */}
                {selectedFile && (
                  <Alert severity="info" sx={{ mt: 1 }}>
                    <Typography variant="caption">
                      <strong>Note:</strong> Vision support varies by model. If your model doesn't support vision, image
                      attachments may be filtered out or cause errors. Ensure your model configuration has vision
                      capability enabled.
                    </Typography>
                  </Alert>
                )}
              </Box>

              {/* Post-processing Toggle */}
              <FormControlLabel
                control={
                  <Switch
                    checked={enablePostProcessing}
                    onChange={(e) => setEnablePostProcessing(e.target.checked)}
                    color="primary"
                    disabled={!selectedConfig?.knowledge_bases?.length}
                  />
                }
                label="Enable Post-processing (requires Knowledge Base)"
                sx={{ mt: 2 }}
              />

              {/* Test Button */}
              <Button
                variant="contained"
                startIcon={streamState.isLoading ? <CircularProgress size={20} color="inherit" /> : <TestIcon />}
                onClick={handleTest}
                disabled={!selectedConfigId || !userMessage.trim() || streamState.isLoading}
                fullWidth
                sx={{ mt: 2 }}
              >
                {streamState.isLoading ? 'Testing...' : 'Test LLM Call'}
              </Button>

              {/* Progress indicator while testing */}
              {streamState.isLoading && (
                <Box sx={{ mt: 2 }}>
                  <LinearProgress />
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ mt: 1, display: 'block', textAlign: 'center' }}
                  >
                    Waiting for LLM response...
                  </Typography>
                </Box>
              )}

              {/* Configuration Summary */}
              {selectedConfig && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="subtitle2" gutterBottom>
                    Configuration Summary:
                  </Typography>
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                    <Chip label={`Config: ${selectedConfig.name}`} size="small" color="primary" variant="outlined" />
                    {selectedConfig.prompt && (
                      <Chip label={`Model Prompt: ${selectedConfig.prompt.name}`} size="small" color="secondary" />
                    )}
                    {selectedConfig.knowledge_bases?.length > 0 && (
                      <Chip label={`${selectedConfig.knowledge_bases.length} KB(s)`} size="small" color="info" />
                    )}
                  </Box>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Results Panel */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Results
              </Typography>

              {streamState.isLoading && (
                <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
                  <CircularProgress />
                </Box>
              )}

              {streamState.error && (
                <Alert
                  severity="error"
                  sx={{ mb: 2 }}
                  icon={streamState.error.isTimeout ? <TimerIcon /> : <ErrorIcon />}
                >
                  {streamState.error.isTimeout ? (
                    <Box>
                      <Typography variant="subtitle2" gutterBottom>
                        Request Timed Out
                      </Typography>
                      <Typography variant="body2">
                        The LLM request timed out after {formatDuration(streamState.error.duration || testDuration)}.
                        This may indicate the model is slow to respond or the server is under heavy load.
                      </Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                        Suggestions: Try a simpler prompt, check your provider connection, or increase the timeout in
                        your configuration.
                      </Typography>
                    </Box>
                  ) : (
                    <Box>
                      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                        {formatError(streamState.error)}
                      </Typography>
                      {streamState.error.duration !== null && streamState.error.duration > 0 && (
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                          Failed after {formatDuration(streamState.error.duration)}
                        </Typography>
                      )}
                    </Box>
                  )}
                </Alert>
              )}

              {llmResult && (
                <Box>
                  {/* Test Duration Summary */}
                  {llmResult.response_time_ms && (
                    <Paper
                      sx={{
                        p: 2,
                        mb: 2,
                        backgroundColor: 'success.light',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                      }}
                    >
                      <TimerIcon color="success" />
                      <Typography variant="body2" color="success.dark">
                        Test completed successfully in <strong>{formatDuration(llmResult.response_time_ms)}</strong>
                      </Typography>
                    </Paper>
                  )}

                  <Tabs value={activeTab} onChange={(_, newValue) => setActiveTab(newValue)}>
                    <Tab label="Final Output" />
                    <Tab label="Request Details" />
                    <Tab label="Raw LLM Response" />
                    {llmResult.post_processing_applied && <Tab label="Post-processing" />}
                  </Tabs>

                  {activeTab === 0 && (
                    <Box sx={{ mt: 2 }}>
                      <Paper sx={{ p: 2, backgroundColor: 'grey.50' }}>
                        <Box display="flex" justifyContent="space-between" alignItems="flex-start" mb={1}>
                          <Typography variant="subtitle2" color="primary">
                            Final Output
                          </Typography>
                          <IconButton
                            size="small"
                            onClick={() => copyToClipboard(llmResult.content)}
                            title="Copy final output"
                          >
                            <CopyIcon />
                          </IconButton>
                        </Box>

                        {/* Show post-processing status */}
                        {llmResult.post_processing_applied !== undefined && (
                          <Box sx={{ mb: 2 }}>
                            <Chip
                              label={llmResult.post_processing_applied ? 'Post-processed' : 'No post-processing'}
                              size="small"
                              color={llmResult.post_processing_applied ? 'success' : 'default'}
                              variant="outlined"
                            />
                            {llmResult.post_processing_reason && (
                              <Chip
                                label={`Reason: ${llmResult.post_processing_reason}`}
                                size="small"
                                color="info"
                                variant="outlined"
                                sx={{ ml: 1 }}
                              />
                            )}
                          </Box>
                        )}

                        <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>
                          {llmResult.content}
                        </Typography>

                        <Box
                          sx={{
                            mt: 2,
                            display: 'flex',
                            gap: 1,
                            flexWrap: 'wrap',
                          }}
                        >
                          <Chip label={`Model: ${llmResult.model}`} size="small" variant="outlined" />
                          <Chip label={`Provider: ${llmResult.provider}`} size="small" variant="outlined" />
                          {llmResult.usage && (
                            <Chip
                              label={`Tokens: ${llmResult.usage.total_tokens || 'N/A'}`}
                              size="small"
                              variant="outlined"
                            />
                          )}
                          {llmResult.response_time_ms && (
                            <Chip
                              icon={<TimerIcon sx={{ fontSize: '1rem' }} />}
                              label={`Duration: ${formatDuration(llmResult.response_time_ms)}`}
                              size="small"
                              variant="outlined"
                              color="success"
                            />
                          )}
                        </Box>
                      </Paper>
                    </Box>
                  )}

                  {activeTab === 1 && (
                    <Box sx={{ mt: 2 }}>
                      <Typography variant="subtitle2" gutterBottom>
                        Request Composition
                      </Typography>
                      <Accordion>
                        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                          <Typography>Messages Sent to LLM</Typography>
                        </AccordionSummary>
                        <AccordionDetails>
                          {formatLLMRequest().messages.map((message, index) => (
                            <Paper key={index} sx={{ p: 2, mb: 1, backgroundColor: 'grey.50' }}>
                              <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
                                <Typography variant="subtitle2" color="primary">
                                  {message.role.toUpperCase()}
                                </Typography>
                                <Chip
                                  label={message.source}
                                  size="small"
                                  color={
                                    message.source === 'Model Prompt'
                                      ? 'secondary'
                                      : message.source.startsWith('KB Prompt')
                                        ? 'info'
                                        : 'default'
                                  }
                                />
                              </Box>
                              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                                {message.content}
                              </Typography>
                            </Paper>
                          ))}
                        </AccordionDetails>
                      </Accordion>

                      {/* Knowledge Base Sources */}
                      {selectedConfig?.knowledge_bases?.length > 0 && (
                        <Box sx={{ mt: 2 }}>
                          {sourcesMutation.isLoading ? (
                            <Box
                              sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 1,
                              }}
                            >
                              <CircularProgress size={16} />
                              <Typography variant="body2" color="text.secondary">
                                Fetching knowledge base sources...
                              </Typography>
                            </Box>
                          ) : sourcesMutation.data ? (
                            <SourcePreview
                              sources={extractDataFromResponse(sourcesMutation.data)?.results || []}
                              title="Knowledge Base Sources Sent to LLM"
                              searchQuery={userMessage}
                            />
                          ) : sourcesMutation.error ? (
                            <Alert severity="error" sx={{ mt: 1 }}>
                              Failed to fetch sources: {formatError(sourcesMutation.error)}
                            </Alert>
                          ) : null}
                        </Box>
                      )}

                      <Box sx={{ mt: 2 }}>
                        <Typography variant="subtitle2" gutterBottom>
                          Request Parameters
                        </Typography>
                        <JSONPretty
                          data={{
                            provider: formatLLMRequest().provider,
                            model: formatLLMRequest().model,
                          }}
                          theme="monokai"
                        />
                      </Box>
                    </Box>
                  )}

                  {activeTab === 2 && (
                    <Box sx={{ mt: 2 }}>
                      <Typography variant="subtitle2" gutterBottom>
                        Raw LLM Response
                      </Typography>
                      {llmResult.raw_content ? (
                        <Paper sx={{ p: 2, backgroundColor: 'grey.50' }}>
                          <Box display="flex" justifyContent="space-between" alignItems="flex-start" mb={1}>
                            <Typography variant="subtitle2" color="secondary">
                              Original LLM Output (before post-processing)
                            </Typography>
                            <IconButton
                              size="small"
                              onClick={() => copyToClipboard(llmResult.raw_content)}
                              title="Copy raw response"
                            >
                              <CopyIcon />
                            </IconButton>
                          </Box>
                          <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>
                            {llmResult.raw_content}
                          </Typography>
                        </Paper>
                      ) : (
                        <Paper sx={{ p: 2, backgroundColor: 'grey.50' }}>
                          <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>
                            {llmResult.content}
                          </Typography>
                          {!llmResult?.post_processing_applied && (
                            <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                              No post-processing was applied - this is the original LLM response.
                            </Typography>
                          )}
                        </Paper>
                      )}
                    </Box>
                  )}

                  {activeTab === 3 && llmResult.post_processing_applied && (
                    <Box sx={{ mt: 2 }}>
                      <Typography variant="subtitle2" gutterBottom>
                        Post-processing Details
                      </Typography>

                      {/* Processing Summary */}
                      <Paper sx={{ p: 2, mb: 2, backgroundColor: 'grey.50' }}>
                        <Typography variant="h6" gutterBottom>
                          Processing Summary
                        </Typography>
                        <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
                          <Chip
                            label={`Status: ${llmResult.post_processing_applied ? 'Applied' : 'Not Applied'}`}
                            color={llmResult.post_processing_applied ? 'success' : 'default'}
                            size="small"
                          />
                          <Chip
                            label={`Reason: ${llmResult.post_processing_reason || 'N/A'}`}
                            color="info"
                            size="small"
                          />
                        </Box>

                        {/* Source Metadata */}
                        {llmResult.source_metadata && llmResult.source_metadata.length > 0 && (
                          <Box>
                            <Typography variant="subtitle2" gutterBottom>
                              Knowledge Base Sources Used ({llmResult.source_metadata.length})
                            </Typography>
                            {llmResult.source_metadata.map((source, index) => (
                              <Paper
                                key={index}
                                sx={{
                                  p: 1,
                                  mb: 1,
                                  backgroundColor: 'background.paper',
                                }}
                              >
                                <Typography variant="body2" fontWeight="bold">
                                  {source.document_title || `Source ${index + 1}`}
                                </Typography>
                                {source.source_url && (
                                  <Typography variant="caption" color="primary">
                                    {source.source_url}
                                  </Typography>
                                )}
                              </Paper>
                            ))}
                          </Box>
                        )}
                      </Paper>

                      {/* Before/After Comparison */}
                      {llmResult.raw_content && (
                        <Box>
                          <Typography variant="subtitle2" gutterBottom>
                            Before/After Comparison
                          </Typography>
                          <Grid container spacing={2}>
                            <Grid item xs={6}>
                              <Paper sx={{ p: 2, backgroundColor: 'grey.50' }}>
                                <Typography variant="subtitle2" color="secondary" gutterBottom>
                                  Before (Raw LLM)
                                </Typography>
                                <Typography
                                  variant="body2"
                                  sx={{
                                    whiteSpace: 'pre-wrap',
                                    fontSize: '0.875rem',
                                  }}
                                >
                                  {llmResult.raw_content}
                                </Typography>
                              </Paper>
                            </Grid>
                            <Grid item xs={6}>
                              <Paper sx={{ p: 2, backgroundColor: 'grey.50' }}>
                                <Typography variant="subtitle2" color="primary" gutterBottom>
                                  After (Post-processed)
                                </Typography>
                                <Typography
                                  variant="body2"
                                  sx={{
                                    whiteSpace: 'pre-wrap',
                                    fontSize: '0.875rem',
                                  }}
                                >
                                  {llmResult.content}
                                </Typography>
                              </Paper>
                            </Grid>
                          </Grid>
                        </Box>
                      )}
                    </Box>
                  )}
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
}

export default LLMTester;
