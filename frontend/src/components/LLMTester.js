import { useState } from 'react';
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
} from '@mui/material';
import {
  PlayArrow as TestIcon,
  ContentCopy as CopyIcon,
  ExpandMore as ExpandMoreIcon,
  SmartToy as ModelIcon,
  Psychology as PromptIcon,
  Storage as KnowledgeBaseIcon,
} from '@mui/icons-material';
import { llmAPI, knowledgeBaseAPI, queryAPI, modelConfigAPI, chatAPI, authAPI, formatError, extractDataFromResponse, extractItemsFromResponse } from '../services/api';
import { promptAPI, ENTITY_TYPES } from '../api/prompts';
import QueryConfiguration from './QueryConfiguration';
import SourcePreview from './SourcePreview';
import PageHelpHeader from './PageHelpHeader';
import JSONPretty from 'react-json-pretty';
import 'react-json-pretty/themes/monikai.css';

import log from '../utils/log';
function LLMTester() {
  // Component state
  const [selectedProvider, setSelectedProvider] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [selectedModelPrompt, setSelectedModelPrompt] = useState('');
  const [selectedKB, setSelectedKB] = useState('');
  const [selectedKBPrompt, setSelectedKBPrompt] = useState('');
  const [userMessage, setUserMessage] = useState('');
  const [enablePostProcessing, setEnablePostProcessing] = useState(true);
  const [streamState, setStreamState] = useState({ isLoading: false, error: null, data: null });
  const [activeTab, setActiveTab] = useState(0);

  // Query configuration state (for knowledge base search)
  const [searchType, setSearchType] = useState('hybrid');
  const [searchLimit, setSearchLimit] = useState(10);
  const [searchThreshold, setSearchThreshold] = useState(null);
  const [titleWeightingEnabled, setTitleWeightingEnabled] = useState(true);
  const [titleWeightMultiplier, setTitleWeightMultiplier] = useState(3.0);

  // Sources state (fetched separately from LLM completion)
  // (removed unused state: retrievedSources, sourcesLoading)

  // Fetch LLM providers
  const { data: providersResponse, isLoading: providersLoading } = useQuery(
    'llm-providers',
    llmAPI.getProviders
  );
  const providers = extractItemsFromResponse(providersResponse);

  // Fetch models for selected provider
  const { data: modelsResponse, isLoading: modelsLoading } = useQuery(
    ['llm-models', selectedProvider],
    () => selectedProvider ? llmAPI.getModels(selectedProvider) : null,
    { enabled: !!selectedProvider }
  );
  const models = extractItemsFromResponse(modelsResponse);

  // Fetch model prompts
  const { data: modelPromptsResponse, isLoading: modelPromptsLoading } = useQuery(
    'model-prompts',
    () => promptAPI.list({ entity_type: ENTITY_TYPES.LLM_MODEL })
  );
  const modelPrompts = extractItemsFromResponse(modelPromptsResponse);

  // Fetch knowledge bases
  const { data: knowledgeBasesResponse, isLoading: kbLoading } = useQuery(
    'knowledge-bases',
    knowledgeBaseAPI.list
  );
  const knowledgeBases = extractItemsFromResponse(knowledgeBasesResponse);

  // Fetch ALL KB prompts (for testing purposes - not just assigned ones)
  const { data: kbPromptsResponse, isLoading: kbPromptsLoading } = useQuery(
    ['all-kb-prompts'],
    () => promptAPI.list({ entity_type: ENTITY_TYPES.KNOWLEDGE_BASE })
  );
  const kbPrompts = extractItemsFromResponse(kbPromptsResponse);

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

  // Handle LLM test
  const handleTest = async () => {
    if (!selectedProvider || !selectedModel || !userMessage.trim()) {
      return;
    }

    // Build messages array
    const messages = [];

    // Add model prompt as system message if selected
    if (selectedModelPrompt) {
      const modelPrompt = modelPrompts.find(p => p.id === selectedModelPrompt);
      if (modelPrompt) {
        messages.push({
          role: 'system',
          content: modelPrompt.content
        });
      }
    }

    // Add KB context if KB and KB prompt are selected
    let contextContent = '';
    if (selectedKB && selectedKBPrompt) {
      // For demo purposes, we'll simulate getting some context
      // In a real implementation, this would query the KB
      contextContent = `[Knowledge Base Context from ${knowledgeBases.find(kb => kb.id === selectedKB)?.name || 'Selected KB'}]

This is where retrieved context from the knowledge base would appear. In the actual implementation, this would be populated by querying the selected knowledge base with the user's message.`;

      const kbPrompt = kbPrompts.find(p => p.id === selectedKBPrompt);
      if (kbPrompt) {
        messages.push({
          role: 'system',
          content: `${kbPrompt.content}\n\nContext:\n${contextContent}`
        });
      }
    }

    // Add user message
    messages.push({
      role: 'user',
      content: userMessage
    });

    // Fetch sources if KB is selected (regardless of post-processing)
    if (selectedKB) {
      sourcesMutation.mutate({
        kbId: selectedKB,
        query: userMessage,
        searchType,
        limit: parseInt(searchLimit),
        threshold: parseFloat(searchThreshold),
        titleWeightingEnabled: titleWeightingEnabled,
        titleWeightMultiplier: titleWeightMultiplier,
      });
    }

    // Streaming flow with SSE
    setStreamState({ isLoading: true, error: null, data: null });
    let modelConfigId = null;
    let conversationId = null;
    try {
      // Recreate the minimal steps from mutation for config + conversation
      let userId = 'llm-tester';
      try {
        const me = await authAPI.getCurrentUser();
        userId = extractDataFromResponse(me)?.id || userId;
      } catch {}
      const name = `LLM Tester - ${new Date().toISOString()}`;
      const cfgResp = await modelConfigAPI.create({
        name,
        description: 'Temporary model configuration generated by LLM Tester',
        llm_provider_id: selectedProvider,
        model_name: selectedModel,
        prompt_id: selectedModelPrompt || null,
        knowledge_base_ids: selectedKB ? [selectedKB] : [],
        kb_prompt_assignments: (selectedKB && selectedKBPrompt) ? [{ knowledge_base_id: selectedKB, prompt_id: selectedKBPrompt }] : [],
        is_active: true,
        created_by: userId,
      });
      modelConfigId = extractDataFromResponse(cfgResp)?.id;
      const convResp = await chatAPI.createConversation({ title: name, model_configuration_id: modelConfigId });
      conversationId = extractDataFromResponse(convResp)?.id;

      // Start stream
      const controller = new AbortController();
      const timeoutMs = Number.parseInt(process.env.REACT_APP_API_TIMEOUT_MS || '90000', 10);
      const timeoutId = setTimeout(() => controller.abort(), Number.isFinite(timeoutMs) ? timeoutMs : 90000);

      const resp = await chatAPI.streamMessage(
        conversationId,
        { message: userMessage, rag_rewrite_mode: selectedKB ? 'raw_query' : 'no_rag' },
        { signal: controller.signal }
      );
      if (!resp.ok || !resp.body) {
        throw new Error(`Streaming failed with status ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let content = '';

      const updateStreamProgress = (contentValue) => {
        setStreamState((prev) => ({
          ...prev,
          data: {
            ...(prev.data || {}),
            content: contentValue,
            model: selectedModel,
            provider: providers.find((p) => p.id === selectedProvider)?.name,
          },
        }));
      };

      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const payload = trimmed.slice(5).trim();
          if (payload === '[DONE]') {
            continue;
          }
          try {
            const obj = JSON.parse(payload);
            const contentStr =
              typeof obj.content === 'string'
                ? obj.content
                : (obj && typeof obj.content === 'object' && typeof obj.content.content === 'string')
                ? obj.content.content
                : '';
            if (contentStr) {
              content += contentStr;
              updateStreamProgress(content);
            }
          } catch {}
        }
        if (done) break;
      }

      clearTimeout(timeoutId);

      // After stream completes, fetch latest assistant message to enrich metadata
      try {
        const msgsResp = await chatAPI.getMessages(conversationId, { page: 1, size: 50 });
        const msgs = extractDataFromResponse(msgsResp) || [];
        const assistantMessages = Array.isArray(msgs) ? msgs.filter(m => m.role === 'assistant') : [];
        const last = assistantMessages[assistantMessages.length - 1];
        const meta = last?.message_metadata || {};
        setStreamState({
          isLoading: false,
          error: null,
          data: {
            content: last?.content || content,
            model: last?.model_id || selectedModel,
            provider: providers.find(p => p.id === selectedProvider)?.name,
            usage: meta?.usage,
            post_processing_applied: !!meta?.has_citations,
            source_metadata: meta?.sources || [],
            raw_content: meta?.raw_content || null,
          }
        });
      } catch {
        setStreamState(prev => ({ ...prev, isLoading: false }));
      }
      return;
    } catch (err) {
      setStreamState({ isLoading: false, error: err, data: null });
      return;
    } finally {
      // Cleanup temporary resources
      try {
        if (conversationId) await chatAPI.deleteConversation(conversationId);
      } catch {}
      try {
        if (modelConfigId) await modelConfigAPI.delete(modelConfigId);
      } catch {}
    }

    // Non-streaming
    // Add search configuration overrides when KB is selected (for testing)

  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
  };

  const formatLLMRequest = () => {
    const messages = [];

    if (selectedModelPrompt) {
      const modelPrompt = modelPrompts.find(p => p.id === selectedModelPrompt);
      if (modelPrompt) {
        messages.push({
          role: 'system',
          content: modelPrompt.content,
          source: 'Model Prompt'
        });
      }
    }

    if (selectedKB && selectedKBPrompt) {
      const kbPrompt = kbPrompts.find(p => p.id === selectedKBPrompt);
      if (kbPrompt) {
        messages.push({
          role: 'system',
          content: `${kbPrompt.content}\n\n[KB Context would be inserted here]`,
          source: 'KB Prompt + Context'
        });
      }
    }

    messages.push({
      role: 'user',
      content: userMessage,
      source: 'User Input'
    });

    return {
      provider: providers.find(p => p.id === selectedProvider)?.name,
      model: selectedModel,
      messages: messages,
    };
  };

  const llmResult = (streamState.data || null);

  return (
    <Box sx={{ p: 3 }}>
      <PageHelpHeader
        title="LLM Tester"
        description="Test LLM calls directly by composing providers, models, prompts, and knowledge bases. Use this to verify model behavior, debug prompts, and experiment with configurations before saving them."
        icon={<PromptIcon />}
        tips={[
          'Select a provider and model, then type a message to test basic completion',
          'Add a system prompt to test how prompts affect the model\'s behavior',
          'Add a Knowledge Base to test RAG—the system will retrieve context and include it',
          'View the Request Preview tab to see exactly what will be sent to the LLM',
          'This creates a temporary Model Configuration—results are for testing only',
        ]}
      />

      <Grid container spacing={3}>
        {/* Configuration Panel */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                <ModelIcon sx={{ mr: 1, verticalAlign: 'middle' }} />
                LLM Configuration
              </Typography>

              {/* Provider Selection */}
              <FormControl fullWidth margin="normal" variant="outlined">
                <InputLabel id="provider-select-label">LLM Provider</InputLabel>
                <Select
                  labelId="provider-select-label"
                  value={selectedProvider}
                  onChange={(e) => {
                    setSelectedProvider(e.target.value);
                    setSelectedModel(''); // Reset model when provider changes
                  }}
                  disabled={providersLoading}
                >
                  {providers.map((provider) => (
                    <MenuItem key={provider.id} value={provider.id}>
                      {provider.name} ({provider.provider_type})
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              {/* Model Selection */}
              <FormControl fullWidth margin="normal" variant="outlined">
                <InputLabel id="model-select-label">Model</InputLabel>
                <Select
                  labelId="model-select-label"
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  disabled={!selectedProvider || modelsLoading}
                >
                  {models.map((model) => (
                    <MenuItem key={model.id} value={model.model_name}>
                      {model.display_name || model.model_name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              <Divider sx={{ my: 2 }} />

              <Typography variant="h6" gutterBottom>
                <PromptIcon sx={{ mr: 1, verticalAlign: 'middle' }} />
                Prompt Configuration
              </Typography>

              {/* Model Prompt Selection */}
              <FormControl fullWidth margin="normal" variant="outlined">
                <InputLabel id="model-prompt-select-label">Model Prompt (Optional)</InputLabel>
                <Select
                  labelId="model-prompt-select-label"
                  value={selectedModelPrompt}
                  onChange={(e) => setSelectedModelPrompt(e.target.value)}
                  disabled={modelPromptsLoading}
                >
                  <MenuItem value="">
                    <span style={{ color: '#999', fontStyle: 'italic' }}>None</span>
                  </MenuItem>
                  {modelPrompts.map((prompt) => (
                    <MenuItem key={prompt.id} value={prompt.id}>
                      {prompt.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              <Divider sx={{ my: 2 }} />

              <Typography variant="h6" gutterBottom>
                <KnowledgeBaseIcon sx={{ mr: 1, verticalAlign: 'middle' }} />
                Knowledge Base Configuration
              </Typography>

              {/* Knowledge Base Selection */}
              <FormControl fullWidth margin="normal" variant="outlined">
                <InputLabel id="kb-select-label">Knowledge Base (Optional)</InputLabel>
                <Select
                  labelId="kb-select-label"
                  value={selectedKB}
                  onChange={(e) => {
                    setSelectedKB(e.target.value);
                    setSelectedKBPrompt(''); // Reset KB prompt when KB changes
                  }}
                  disabled={kbLoading}
                >
                  <MenuItem value="">
                    <span style={{ color: '#999', fontStyle: 'italic' }}>None</span>
                  </MenuItem>
                  {knowledgeBases.map((kb) => (
                    <MenuItem key={kb.id} value={kb.id}>
                      {kb.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              {/* KB Prompt Selection */}
              <FormControl fullWidth margin="normal" variant="outlined">
                <InputLabel id="kb-prompt-select-label">KB Prompt (Optional)</InputLabel>
                <Select
                  labelId="kb-prompt-select-label"
                  value={selectedKBPrompt}
                  onChange={(e) => setSelectedKBPrompt(e.target.value)}
                  disabled={kbPromptsLoading}
                >
                  <MenuItem value="">
                    <span style={{ color: '#999', fontStyle: 'italic' }}>None</span>
                  </MenuItem>
                  {kbPrompts.length === 0 && !kbPromptsLoading && (
                    <MenuItem disabled>
                      <span style={{ color: '#999', fontStyle: 'italic' }}>No KB prompts available</span>
                    </MenuItem>
                  )}
                  {kbPrompts.map((prompt) => (
                    <MenuItem key={prompt.id} value={prompt.id}>
                      {prompt.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              {/* Query Configuration - only show when KB is selected */}
              {selectedKB && (
                <Box sx={{ mt: 2 }}>
                  <Divider sx={{ mb: 2 }}>
                    <Typography variant="caption" color="text.secondary">
                      Search Configuration
                    </Typography>
                  </Divider>

                  <QueryConfiguration
                    selectedKB={selectedKB}
                    onKBChange={setSelectedKB}
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
                    showKBSelector={false}  // Already have KB selector above
                    showQueryText={false}   // Will use the main user message field
                    queryTextLabel="Search Query"
                    queryTextPlaceholder="This will use the user message above for search..."
                    queryTextRows={2}

                    // Pass through data
                    kbLoading={kbLoading}
                    knowledgeBases={knowledgeBases}
                  />
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

              {/* Post-processing Toggle */}
              <FormControlLabel
                control={
                  <Switch
                    checked={enablePostProcessing}
                    onChange={(e) => setEnablePostProcessing(e.target.checked)}
                    color="primary"
                    disabled={!selectedKB}
                  />
                }
                label="Enable Post-processing (requires Knowledge Base)"
                sx={{ mt: 2 }}
              />

              {/* Test Button */}
              <Button
                variant="contained"
                startIcon={<TestIcon />}
                onClick={handleTest}
                disabled={!selectedProvider || !selectedModel || !userMessage.trim() || streamState.isLoading}
                fullWidth
                sx={{ mt: 2 }}
              >
                {'Test LLM Call'}
              </Button>

              {/* Configuration Summary */}
              <Box sx={{ mt: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  Configuration Summary:
                </Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                  {selectedProvider && (
                    <Chip
                      label={`Provider: ${providers.find(p => p.id === selectedProvider)?.name}`}
                      size="small"
                      color="primary"
                    />
                  )}
                  {selectedModel && (
                    <Chip
                      label={`Model: ${selectedModel}`}
                      size="small"
                      color="primary"
                    />
                  )}
                  {selectedModelPrompt && (
                    <Chip
                      label={`Model Prompt: ${modelPrompts.find(p => p.id === selectedModelPrompt)?.name}`}
                      size="small"
                      color="secondary"
                    />
                  )}
                  {selectedKB && (
                    <Chip
                      label={`KB: ${knowledgeBases.find(kb => kb.id === selectedKB)?.name}`}
                      size="small"
                      color="info"
                    />
                  )}
                  {selectedKBPrompt && (
                    <Chip
                      label={`KB Prompt: ${kbPrompts.find(p => p.id === selectedKBPrompt)?.name}`}
                      size="small"
                      color="info"
                    />
                  )}
                </Box>
              </Box>
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
                <Alert severity="error" sx={{ mb: 2 }}>
                  Error: {formatError(streamState.error)}
                </Alert>
              )}

              {llmResult && (
                <Box>
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

                        <Box sx={{ mt: 2, display: 'flex', gap: 1 }}>
                          <Chip
                            label={`Model: ${llmResult.model}`}
                            size="small"
                            variant="outlined"
                          />
                          <Chip
                            label={`Provider: ${llmResult.provider}`}
                            size="small"
                            variant="outlined"
                          />
                          {llmResult.usage && (
                            <Chip
                              label={`Tokens: ${llmResult.usage.total_tokens || 'N/A'}`}
                              size="small"
                              variant="outlined"
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
                                    message.source === 'Model Prompt' ? 'secondary' :
                                    message.source === 'KB Prompt + Context' ? 'info' :
                                    'default'
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
                      {selectedKB && (
                        <Box sx={{ mt: 2 }}>
                          {sourcesMutation.isLoading ? (
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
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
                              <Paper key={index} sx={{ p: 1, mb: 1, backgroundColor: 'background.paper' }}>
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
                                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', fontSize: '0.875rem' }}>
                                  {llmResult.raw_content}
                                </Typography>
                              </Paper>
                            </Grid>
                            <Grid item xs={6}>
                              <Paper sx={{ p: 2, backgroundColor: 'grey.50' }}>
                                <Typography variant="subtitle2" color="primary" gutterBottom>
                                  After (Post-processed)
                                </Typography>
                                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', fontSize: '0.875rem' }}>
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
