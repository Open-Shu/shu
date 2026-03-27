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
  Chip,
  Alert,
  CircularProgress,
  Tabs,
  Tab,
  FormControlLabel,
  Switch,
  Slider,
  Divider,
  Snackbar,
  IconButton,
  Tooltip,
} from '@mui/material';
import { Search as SearchIcon, ContentCopy as ContentCopyIcon } from '@mui/icons-material';
import {
  knowledgeBaseAPI,
  queryAPI,
  formatError,
  extractDataFromResponse,
  extractItemsFromResponse,
} from '../services/api';
import SourcePreview from './SourcePreview';
import PageHelpHeader from './PageHelpHeader';
import JSONPretty from 'react-json-pretty';
import 'react-json-pretty/themes/monikai.css';

import { log } from '../utils/log';
import { formatResultsForJudgment } from '../utils/exportForJudgment';

const RAG_MODE_OPTIONS = [
  { value: 'no_rag', label: 'No RAG (model only)' },
  { value: 'raw_query', label: 'Raw Query (pass-through)' },
  { value: 'distill_context', label: 'Distill Query (key facts only)' },
  { value: 'rewrite_enhanced', label: 'Rewrite & Enhance (LLM optimized)' },
];

function QueryTester() {
  const [selectedKB, setSelectedKB] = useState('');
  const [queryText, setQueryText] = useState('');
  const [searchType, setSearchType] = useState('similarity');
  const [limit, setLimit] = useState(10);
  const [threshold, setThreshold] = useState(null); // Will be set from KB config
  const [titleWeightingEnabled, setTitleWeightingEnabled] = useState(true);
  const [titleWeightMultiplier, setTitleWeightMultiplier] = useState(3.0);
  const [activeTab, setActiveTab] = useState(0);
  const [ragRewriteMode, setRagRewriteMode] = useState('raw_query');
  const [snackbar, setSnackbar] = useState({ open: false, message: '' });
  const [exportLoading, setExportLoading] = useState(false);
  // Multi-surface search weights and fusion
  const [chunkVectorWeight, setChunkVectorWeight] = useState(0.25);
  const [queryMatchWeight, setQueryMatchWeight] = useState(0.2);
  const [synopsisMatchWeight, setSynopsisMatchWeight] = useState(0.15);
  const [bm25Weight, setBm25Weight] = useState(0.15);
  const [chunkSummaryWeight, setChunkSummaryWeight] = useState(0.25);
  const [fusionFormula, setFusionFormula] = useState('max_sqrt_mean_max');

  const { data: knowledgeBasesResponse, isLoading: kbLoading } = useQuery('knowledgeBases', knowledgeBaseAPI.list);

  // Extract knowledge bases data from envelope format
  const knowledgeBases = extractItemsFromResponse(knowledgeBasesResponse);

  // Fetch KB config when selectedKB changes to get default threshold and title weighting
  useQuery(['kb-config', selectedKB], () => (selectedKB ? knowledgeBaseAPI.getRAGConfig(selectedKB) : null), {
    enabled: !!selectedKB,
    onSuccess: (data) => {
      if (data) {
        const config = extractDataFromResponse(data);
        // Always update from KB config when KB changes
        setThreshold(config.search_threshold || 0.3);
        setTitleWeightingEnabled(config.title_weighting_enabled ?? true);
        setTitleWeightMultiplier(config.title_weight_multiplier || 3.0);
      }
    },
  });

  const queryMutation = useMutation(
    (params) => {
      const basePayload = {
        query: params.query,
        limit: params.limit,
        rag_rewrite_mode: params.ragRewriteMode || 'raw_query',
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

      if (params.searchType === 'multi_surface') {
        return queryAPI.search(params.kbId, {
          ...basePayload,
          query_type: 'multi_surface',
          similarity_threshold: params.threshold,
          chunk_vector_weight: params.chunkVectorWeight,
          query_match_weight: params.queryMatchWeight,
          synopsis_match_weight: params.synopsisMatchWeight,
          bm25_weight: params.bm25Weight,
          chunk_summary_weight: params.chunkSummaryWeight,
          fusion_formula: params.fusionFormula,
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
        log.error('Query error:', error);
      },
    }
  );

  const handleSearch = () => {
    if (!selectedKB || !queryText.trim()) {
      return;
    }

    queryMutation.mutate({
      kbId: selectedKB,
      query: queryText,
      searchType,
      limit: parseInt(limit),
      threshold: parseFloat(threshold),
      titleWeightingEnabled: titleWeightingEnabled,
      titleWeightMultiplier: titleWeightMultiplier,
      ragRewriteMode,
      chunkVectorWeight: chunkVectorWeight,
      queryMatchWeight: queryMatchWeight,
      synopsisMatchWeight: synopsisMatchWeight,
      bm25Weight: bm25Weight,
      chunkSummaryWeight: chunkSummaryWeight,
      fusionFormula: fusionFormula,
    });
  };

  const formatQueryRequest = () => {
    const baseRequest = {
      query: queryText,
      limit: parseInt(limit),
      rag_rewrite_mode: ragRewriteMode,
    };

    if (searchType === 'similarity') {
      return {
        ...baseRequest,
        query_type: 'similarity',
        similarity_threshold: parseFloat(threshold),
      };
    } else if (searchType === 'keyword') {
      return {
        ...baseRequest,
        query_type: 'keyword',
        similarity_threshold: parseFloat(threshold),
        title_weighting_enabled: titleWeightingEnabled,
        title_weight_multiplier: titleWeightingEnabled ? titleWeightMultiplier : 1.0,
      };
    } else if (searchType === 'multi_surface') {
      return {
        ...baseRequest,
        query_type: 'multi_surface',
        similarity_threshold: parseFloat(threshold),
        chunk_vector_weight: chunkVectorWeight,
        query_match_weight: queryMatchWeight,
        synopsis_match_weight: synopsisMatchWeight,
        bm25_weight: bm25Weight,
        chunk_summary_weight: chunkSummaryWeight,
        fusion_formula: fusionFormula,
      };
    } else {
      return {
        ...baseRequest,
        query_type: 'hybrid',
        similarity_threshold: parseFloat(threshold),
        title_weighting_enabled: titleWeightingEnabled,
        title_weight_multiplier: titleWeightingEnabled ? titleWeightMultiplier : 1.0,
      };
    }
  };

  const handleExportForJudgment = async () => {
    if (!queryResults?.multi_surface_results || !selectedKB) {
      return;
    }
    setExportLoading(true);
    try {
      const topN = parseInt(limit);

      // Run a separate similarity search to get true baseline results
      const baselineResponse = await queryAPI.search(selectedKB, {
        query: queryText,
        limit: topN,
        query_type: 'similarity',
        similarity_threshold: parseFloat(threshold),
        rag_rewrite_mode: 'raw_query',
      });
      const baselineData = extractDataFromResponse(baselineResponse);
      const baselineResults = baselineData?.results || [];

      // MS results already have full content, summary, matched_query from backend
      const text = formatResultsForJudgment(queryText, baselineResults, queryResults.multi_surface_results, topN);
      await navigator.clipboard.writeText(text);
      setSnackbar({ open: true, message: 'Exported to clipboard' });
    } catch (err) {
      log.error('Export for judgment failed:', err);
      setSnackbar({ open: true, message: 'Export failed — see console' });
    } finally {
      setExportLoading(false);
    }
  };

  // Extract query results from envelope format
  const queryResults = extractDataFromResponse(queryMutation.data);

  return (
    <Box>
      <PageHelpHeader
        title="Query Tester"
        description="Test vector search and retrieval against your Knowledge Bases. Use this tool to debug search quality, tune thresholds, and understand how RAG retrieval works."
        icon={<SearchIcon />}
        tips={[
          'Select a Knowledge Base, enter a query, and click Search to see retrieved chunks',
          'Similarity search uses pure vector matching; Hybrid adds keyword boosting',
          'Lower the similarity threshold to retrieve more (but potentially less relevant) results',
          'Enable title weighting to boost chunks from documents with matching titles',
          'Use RAG rewrite modes to see how query preprocessing affects results',
        ]}
      />

      <Grid container spacing={3}>
        {/* Query Configuration */}
        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Query Configuration
              </Typography>

              <FormControl fullWidth sx={{ mb: 2 }}>
                <InputLabel
                  sx={{
                    backgroundColor: 'background.paper',
                    px: 0.5,
                    '&.Mui-focused': {
                      backgroundColor: 'background.paper',
                    },
                  }}
                >
                  Knowledge Base
                </InputLabel>
                <Select value={selectedKB} onChange={(e) => setSelectedKB(e.target.value)} disabled={kbLoading}>
                  {knowledgeBases?.map((kb) => (
                    <MenuItem key={kb.id} value={kb.id}>
                      {kb.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              <FormControl fullWidth sx={{ mb: 2 }}>
                <InputLabel
                  sx={{
                    backgroundColor: 'background.paper',
                    px: 0.5,
                    '&.Mui-focused': {
                      backgroundColor: 'background.paper',
                    },
                  }}
                >
                  Search Type
                </InputLabel>
                <Select value={searchType} onChange={(e) => setSearchType(e.target.value)}>
                  <MenuItem value="similarity">Similarity Search</MenuItem>
                  <MenuItem value="keyword">Keyword Search</MenuItem>
                  <MenuItem value="hybrid">Hybrid Search</MenuItem>
                  <MenuItem value="multi_surface">Multi-Surface Search</MenuItem>
                </Select>
              </FormControl>

              <TextField
                fullWidth
                label="Query Text"
                multiline
                rows={4}
                value={queryText}
                onChange={(e) => setQueryText(e.target.value)}
                placeholder="Enter your search query..."
                sx={{
                  mb: 2,
                  '& .MuiInputBase-input::placeholder': {
                    color: '#9ca3af',
                    opacity: 0.7,
                    fontStyle: 'italic',
                  },
                }}
              />

              <TextField
                fullWidth
                type="number"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                placeholder="Limit (e.g., 10)"
                sx={{ mb: 2 }}
                inputProps={{ min: 1, max: 100 }}
              />

              {(searchType === 'similarity' ||
                searchType === 'keyword' ||
                searchType === 'hybrid' ||
                searchType === 'multi_surface') && (
                <TextField
                  fullWidth
                  type="number"
                  value={threshold || ''}
                  onChange={(e) => setThreshold(e.target.value)}
                  placeholder="Threshold (e.g., 0.3)"
                  sx={{ mb: 2 }}
                  inputProps={{ min: 0, max: 1, step: 0.1 }}
                  helperText={
                    searchType === 'similarity'
                      ? 'Similarity threshold (0.0 - 1.0)'
                      : searchType === 'keyword'
                        ? 'Score threshold (0.0 - 1.0)'
                        : 'Score threshold (0.0 - 1.0)'
                  }
                />
              )}

              <FormControl fullWidth sx={{ mb: 2 }}>
                <InputLabel
                  id="query-tester-rag-mode-label"
                  sx={{
                    backgroundColor: 'background.paper',
                    px: 0.5,
                    '&.Mui-focused': {
                      backgroundColor: 'background.paper',
                    },
                  }}
                >
                  RAG Query Mode
                </InputLabel>
                <Select
                  labelId="query-tester-rag-mode-label"
                  id="query-tester-rag-mode"
                  value={ragRewriteMode}
                  label="RAG Query Mode"
                  onChange={(e) => setRagRewriteMode(e.target.value)}
                >
                  {RAG_MODE_OPTIONS.map((option) => (
                    <MenuItem key={option.value} value={option.value}>
                      {option.label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              {/* Title Weighting Controls */}
              {(searchType === 'keyword' || searchType === 'hybrid') && (
                <Box sx={{ mb: 2 }}>
                  <Divider sx={{ mb: 2 }}>
                    <Typography variant="caption" color="text.secondary">
                      Title Weighting
                    </Typography>
                  </Divider>

                  <FormControlLabel
                    control={
                      <Switch
                        checked={titleWeightingEnabled}
                        onChange={(e) => setTitleWeightingEnabled(e.target.checked)}
                        color="primary"
                      />
                    }
                    label="Enable Title Weighting"
                    sx={{ mb: 2, display: 'block' }}
                  />

                  {titleWeightingEnabled && (
                    <Box sx={{ px: 1 }}>
                      <Typography variant="body2" color="text.secondary" gutterBottom>
                        Title Weight Multiplier: {titleWeightMultiplier}x
                      </Typography>
                      <Slider
                        value={titleWeightMultiplier}
                        onChange={(_, newValue) => setTitleWeightMultiplier(newValue)}
                        min={1.0}
                        max={10.0}
                        step={0.5}
                        marks={[
                          { value: 1.0, label: '1x' },
                          { value: 3.0, label: '3x' },
                          { value: 5.0, label: '5x' },
                          { value: 10.0, label: '10x' },
                        ]}
                        valueLabelDisplay="auto"
                        sx={{ mb: 1 }}
                      />
                      <Typography variant="caption" color="text.secondary">
                        Higher values boost documents with matching titles more strongly
                      </Typography>
                    </Box>
                  )}
                </Box>
              )}

              {/* Multi-Surface Weight Controls */}
              {searchType === 'multi_surface' && (
                <Box sx={{ mb: 2 }}>
                  <Divider sx={{ mb: 2 }}>
                    <Typography variant="caption" color="text.secondary">
                      Surface Weights
                    </Typography>
                  </Divider>

                  <Box sx={{ px: 1 }}>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      Chunk Vector: {chunkVectorWeight.toFixed(2)}
                    </Typography>
                    <Slider
                      value={chunkVectorWeight}
                      onChange={(_, newValue) => setChunkVectorWeight(newValue)}
                      min={0}
                      max={1}
                      step={0.05}
                      valueLabelDisplay="auto"
                      sx={{ mb: 2 }}
                    />

                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      Query Match: {queryMatchWeight.toFixed(2)}
                    </Typography>
                    <Slider
                      value={queryMatchWeight}
                      onChange={(_, newValue) => setQueryMatchWeight(newValue)}
                      min={0}
                      max={1}
                      step={0.05}
                      valueLabelDisplay="auto"
                      sx={{ mb: 2 }}
                    />

                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      Synopsis Match: {synopsisMatchWeight.toFixed(2)}
                    </Typography>
                    <Slider
                      value={synopsisMatchWeight}
                      onChange={(_, newValue) => setSynopsisMatchWeight(newValue)}
                      min={0}
                      max={1}
                      step={0.05}
                      valueLabelDisplay="auto"
                      sx={{ mb: 2 }}
                    />

                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      Keyword Search: {bm25Weight.toFixed(2)}
                    </Typography>
                    <Slider
                      value={bm25Weight}
                      onChange={(_, newValue) => setBm25Weight(newValue)}
                      min={0}
                      max={1}
                      step={0.05}
                      valueLabelDisplay="auto"
                      sx={{ mb: 2 }}
                    />

                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      Chunk Summary: {chunkSummaryWeight.toFixed(2)}
                    </Typography>
                    <Slider
                      value={chunkSummaryWeight}
                      onChange={(_, newValue) => setChunkSummaryWeight(newValue)}
                      min={0}
                      max={1}
                      step={0.05}
                      valueLabelDisplay="auto"
                      sx={{ mb: 1 }}
                    />

                    <Typography variant="caption" color="text.secondary">
                      Adjust weights to control how much each surface contributes to the final score
                    </Typography>
                  </Box>

                  <Divider sx={{ my: 2 }}>
                    <Typography variant="caption" color="text.secondary">
                      Fusion Formula
                    </Typography>
                  </Divider>

                  <FormControl fullWidth size="small">
                    <Select value={fusionFormula} onChange={(e) => setFusionFormula(e.target.value)}>
                      <MenuItem value="max_sqrt_mean_max">Max × √(mean/max)</MenuItem>
                      <MenuItem value="weighted_average">Weighted Average</MenuItem>
                    </Select>
                  </FormControl>
                </Box>
              )}

              <Button
                fullWidth
                variant="contained"
                startIcon={<SearchIcon />}
                onClick={handleSearch}
                disabled={!selectedKB || !queryText.trim() || queryMutation.isLoading}
              >
                {queryMutation.isLoading ? <CircularProgress size={20} /> : 'Search'}
              </Button>
            </CardContent>
          </Card>
        </Grid>

        {/* Results */}
        <Grid item xs={12} md={8}>
          <Card>
            <CardContent>
              <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
                <Typography variant="h6">Results</Typography>
                <Box display="flex" gap={1} alignItems="center">
                  {queryResults?.multi_surface_results?.length > 0 && (
                    <Tooltip title="Export for LLM Judgment">
                      <IconButton size="small" onClick={handleExportForJudgment} disabled={exportLoading}>
                        {exportLoading ? <CircularProgress size={18} /> : <ContentCopyIcon fontSize="small" />}
                      </IconButton>
                    </Tooltip>
                  )}
                  {queryResults && (
                    <Chip
                      label={`${queryResults.multi_surface_results?.length || queryResults.results?.length || 0} results`}
                      color="primary"
                      size="small"
                    />
                  )}
                </Box>
              </Box>

              {queryMutation.isLoading && (
                <Box display="flex" justifyContent="center" p={3}>
                  <CircularProgress />
                </Box>
              )}

              {queryMutation.error && (
                <Alert severity="error" sx={{ mb: 2 }}>
                  {formatError(queryMutation.error).message}
                </Alert>
              )}

              {queryResults && !queryMutation.isLoading && (
                <Box>
                  {queryResults.rag_query && (
                    <Alert severity={queryResults.rag_query.used ? 'info' : 'warning'} sx={{ mb: 2 }}>
                      <Typography variant="subtitle2">RAG Query Diagnostics</Typography>
                      <Typography variant="body2" sx={{ mt: 0.5 }}>
                        <strong>Original:</strong> {queryResults.rag_query.original || '(empty)'}
                      </Typography>
                      <Typography variant="body2">
                        <strong>Rewritten:</strong> {queryResults.rag_query.rewritten || '(empty)'}
                      </Typography>
                      <Typography variant="caption" display="block" sx={{ mt: 0.5 }}>
                        {queryResults.rag_query.used
                          ? 'Rewritten query was sent to the retriever.'
                          : 'Original query was used (rewrite disabled or failed).'}
                      </Typography>
                    </Alert>
                  )}
                  <Tabs value={activeTab} onChange={(_, newValue) => setActiveTab(newValue)} sx={{ mb: 2 }}>
                    <Tab label="Results" />
                    <Tab label="Request" />
                    <Tab label="Response" />
                  </Tabs>

                  {activeTab === 0 && (
                    <Box>
                      {queryResults.multi_surface_results && queryResults.multi_surface_results.length > 0 ? (
                        <SourcePreview
                          sources={queryResults.multi_surface_results}
                          title="Multi-Surface Results"
                          searchQuery={queryText}
                          knowledgeBaseId={selectedKB}
                        />
                      ) : queryResults.results && queryResults.results.length > 0 ? (
                        <SourcePreview
                          sources={queryResults.results}
                          title="Query Results"
                          searchQuery={queryText}
                          knowledgeBaseId={selectedKB}
                        />
                      ) : (
                        <Alert severity="info">No results found for this query.</Alert>
                      )}
                    </Box>
                  )}

                  {activeTab === 1 && (
                    <Box sx={{ width: '100%', overflow: 'hidden' }}>
                      <Typography variant="subtitle2" gutterBottom>
                        Query Request
                      </Typography>
                      <Box
                        sx={{
                          overflowX: 'auto',
                          maxWidth: '100%',
                          '& pre': { whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
                        }}
                      >
                        <JSONPretty data={formatQueryRequest()} theme="monokai" />
                      </Box>
                    </Box>
                  )}

                  {activeTab === 2 && (
                    <Box sx={{ width: '100%', overflow: 'hidden' }}>
                      <Typography variant="subtitle2" gutterBottom>
                        Full Response
                      </Typography>
                      <Box
                        sx={{
                          overflowX: 'auto',
                          maxWidth: '100%',
                          '& pre': { whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
                        }}
                      >
                        <JSONPretty data={queryResults} theme="monokai" />
                      </Box>
                    </Box>
                  )}
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
        onClose={() => setSnackbar({ open: false, message: '' })}
        message={snackbar.message}
      />
    </Box>
  );
}

export default QueryTester;
