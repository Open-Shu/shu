import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  FormControl,
  FormLabel,
  RadioGroup,
  FormControlLabel,
  Radio,
  Switch,
  Box,
  Typography,
  IconButton,
  Alert,
  Divider,
  Slider,
  Grid,
} from "@mui/material";
import HelpTooltip from "./HelpTooltip.jsx";
import { Save as SaveIcon, Refresh as RefreshIcon } from "@mui/icons-material";
import { useMutation, useQuery } from "react-query";
import {
  knowledgeBaseAPI,
  formatError,
  extractDataFromResponse,
} from "../services/api";
import { log } from "../utils/log";

const KBConfigDialog = ({ open, onClose, knowledgeBase }) => {
  // Initial state - will be populated from backend ConfigurationManager
  // Backend provides proper defaults via configuration cascade
  const [config, setConfig] = useState({
    include_references: true,
    reference_format: "markdown",
    context_format: "detailed",
    prompt_template: "custom",
    search_threshold: 0.7, // Default value to prevent uncontrolled component issues
    max_results: 10, // Default value to prevent uncontrolled component issues
    chunk_overlap_ratio: 0.2, // Default value to prevent uncontrolled component issues
    search_type: "hybrid",
    // Title Search Configuration
    title_weighting_enabled: true,
    title_weight_multiplier: 3.0,
    title_chunk_enabled: true,
    max_chunks_per_document: 4,
    // Full Document Escalation (defaults only to prevent uncontrolled component issues; real defaults come from backend)
    fetch_full_documents: false,
    full_doc_max_docs: 2,
    full_doc_token_cap: 80000,
  });
  const [hasChanges, setHasChanges] = useState(false);

  // Fetch current RAG configuration
  const { isLoading, error, refetch } = useQuery(
    ["ragConfig", knowledgeBase?.id],
    () =>
      knowledgeBase ? knowledgeBaseAPI.getRAGConfig(knowledgeBase.id) : null,
    {
      enabled: !!knowledgeBase?.id && open,
      staleTime: 0, // Always consider data stale
      cacheTime: 0, // Don't cache the data
      refetchOnWindowFocus: true,
      onSuccess: (response) => {
        log.debug("RAG Config fetched:", response);
        const data = extractDataFromResponse(response);
        if (data) {
          // Backend now provides proper defaults via ConfigurationManager
          // No need for hardcoded fallbacks in frontend
          setConfig({
            include_references: data.include_references ?? true,
            reference_format: data.reference_format || "markdown",
            context_format: data.context_format || "detailed",
            prompt_template: data.prompt_template || "custom",
            search_threshold: data.search_threshold ?? 0.7,
            max_results: data.max_results ?? 10,
            chunk_overlap_ratio: data.chunk_overlap_ratio ?? 0.2,
            search_type: data.search_type || "hybrid",
            // Title Search Configuration
            title_weighting_enabled: data.title_weighting_enabled ?? true,
            title_weight_multiplier: data.title_weight_multiplier ?? 3.0,
            title_chunk_enabled: data.title_chunk_enabled ?? true,
            max_chunks_per_document: data.max_chunks_per_document ?? 4,
            // Full Document Escalation
            fetch_full_documents: data.fetch_full_documents ?? false,
            full_doc_max_docs: data.full_doc_max_docs ?? 2,
            full_doc_token_cap: data.full_doc_token_cap ?? 80000,
          });
          setHasChanges(false);
        }
      },
    },
  );

  // Update RAG configuration mutation
  const updateConfigMutation = useMutation(
    (configData) =>
      knowledgeBaseAPI.updateRAGConfig(knowledgeBase.id, configData),
    {
      onSuccess: async (response) => {
        log.info("RAG Config saved:", response);
        // Update the local state with the response data
        const data = extractDataFromResponse(response);
        if (data) {
          // Backend now provides proper defaults via ConfigurationManager
          // No need for hardcoded fallbacks in frontend
          setConfig({
            include_references: data.include_references ?? true,
            reference_format: data.reference_format || "markdown",
            context_format: data.context_format || "detailed",
            prompt_template: data.prompt_template || "custom",
            search_threshold: data.search_threshold ?? 0.7,
            max_results: data.max_results ?? 10,
            chunk_overlap_ratio: data.chunk_overlap_ratio ?? 0.2,
            search_type: data.search_type || "hybrid",
            // Title Search Configuration
            title_weighting_enabled: data.title_weighting_enabled ?? true,
            title_weight_multiplier: data.title_weight_multiplier ?? 3.0,
            title_chunk_enabled: data.title_chunk_enabled ?? true,
            max_chunks_per_document: data.max_chunks_per_document ?? 4,
            // Full Document Escalation
            fetch_full_documents: data.fetch_full_documents ?? false,
            full_doc_max_docs: data.full_doc_max_docs ?? 2,
            full_doc_token_cap: data.full_doc_token_cap ?? 80000,
          });
        }

        // No need to invalidate and refetch since we already updated local state
        // with the response data from the backend
        setHasChanges(false);

        // Don't close immediately - let user see the updated values
        // onClose();
      },
    },
  );

  const handleConfigChange = (field, value) => {
    setConfig((prev) => ({ ...prev, [field]: value }));
    setHasChanges(true);
  };

  const handleSave = () => {
    log.info("Saving config:", config);
    updateConfigMutation.mutate(config);
  };

  const handleReset = () => {
    refetch();
  };

  if (!knowledgeBase) {
    return null;
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Typography variant="h6">
            Configure RAG Settings - {knowledgeBase.name}
          </Typography>
          <IconButton onClick={handleReset} disabled={isLoading}>
            <RefreshIcon />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent>
        {isLoading && (
          <Alert severity="info" sx={{ mb: 2 }}>
            Loading configuration...
          </Alert>
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            Error loading configuration: {formatError(error)}
          </Alert>
        )}

        {updateConfigMutation.error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            Error saving configuration:{" "}
            {formatError(updateConfigMutation.error)}
          </Alert>
        )}

        <Box sx={{ mt: 2 }}>
          {/* Search & Retrieval Settings */}
          <Typography variant="h6" gutterBottom>
            Search & Retrieval Settings
            <HelpTooltip title="Configure how the system searches and retrieves relevant content from your knowledge base" />
          </Typography>

          <Grid container spacing={3} sx={{ mb: 3 }}>
            <Grid item xs={12} sm={6}>
              <FormControl fullWidth>
                <FormLabel>
                  Search Similarity Threshold
                  <HelpTooltip title="Minimum similarity score (0.0-1.0) for content to be considered relevant. Higher values = more precise but fewer results." />
                </FormLabel>
                <Box sx={{ px: 2, mt: 1 }}>
                  <Slider
                    value={config.search_threshold}
                    onChange={(_, value) =>
                      handleConfigChange("search_threshold", value)
                    }
                    min={0.1}
                    max={1.0}
                    step={0.05}
                    marks={[
                      { value: 0.1, label: "0.1 (Broad)" },
                      { value: 0.5, label: "0.5" },
                      { value: 0.9, label: "0.9 (Precise)" },
                    ]}
                    valueLabelDisplay="on"
                  />
                </Box>
              </FormControl>
            </Grid>

            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                label="Maximum Results"
                type="number"
                value={config.max_results}
                onChange={(e) =>
                  handleConfigChange(
                    "max_results",
                    parseInt(e.target.value) || 10,
                  )
                }
                inputProps={{ min: 1, max: 50 }}
                helperText="Maximum number of relevant chunks to retrieve (1-50)"
              />
            </Grid>

            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                label="Max Chunks Per Document"
                type="number"
                value={config.max_chunks_per_document}
                onChange={(e) =>
                  handleConfigChange(
                    "max_chunks_per_document",
                    parseInt(e.target.value) || 4,
                  )
                }
                inputProps={{ min: 1, max: 10 }}
                helperText="Maximum chunks to return from each document (1-10)"
              />
            </Grid>
          </Grid>

          <Grid container spacing={3} sx={{ mb: 3 }}>
            <Grid item xs={12} sm={6}>
              <FormControl component="fieldset">
                <FormLabel component="legend">
                  Search Type
                  <HelpTooltip title="'Similarity' uses semantic search, 'Keyword' uses text matching, 'Hybrid' combines both approaches" />
                </FormLabel>
                <RadioGroup
                  value={config.search_type}
                  onChange={(e) =>
                    handleConfigChange("search_type", e.target.value)
                  }
                >
                  <FormControlLabel
                    value="similarity"
                    control={<Radio />}
                    label="Similarity (semantic)"
                  />
                  <FormControlLabel
                    value="keyword"
                    control={<Radio />}
                    label="Keyword (text matching)"
                  />
                  <FormControlLabel
                    value="hybrid"
                    control={<Radio />}
                    label="Hybrid (combined)"
                  />
                </RadioGroup>
              </FormControl>
            </Grid>
          </Grid>

          <Divider sx={{ my: 3 }} />

          {/* Title Search Configuration */}
          <Typography variant="h6" gutterBottom>
            Title Search Enhancement
            <HelpTooltip title="Configure how document titles are weighted in search results to improve document discovery" />
          </Typography>

          <Grid container spacing={3} sx={{ mb: 3 }}>
            <Grid item xs={12} sm={6}>
              <FormControlLabel
                control={
                  <Switch
                    checked={config.title_weighting_enabled}
                    onChange={(e) =>
                      handleConfigChange(
                        "title_weighting_enabled",
                        e.target.checked,
                      )
                    }
                  />
                }
                label={
                  <Box>
                    <Typography variant="body1">
                      Enable Title Weighting
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Boost search scores for documents with titles matching the
                      query
                    </Typography>
                  </Box>
                }
              />
            </Grid>

            <Grid item xs={12} sm={6}>
              <FormControlLabel
                control={
                  <Switch
                    checked={config.title_chunk_enabled}
                    onChange={(e) =>
                      handleConfigChange(
                        "title_chunk_enabled",
                        e.target.checked,
                      )
                    }
                  />
                }
                label={
                  <Box>
                    <Typography variant="body1">Create Title Chunks</Typography>
                    <Typography variant="body2" color="text.secondary">
                      Create dedicated searchable chunks from document titles
                    </Typography>
                  </Box>
                }
              />
            </Grid>

            <Grid item xs={12}>
              <Typography gutterBottom>
                Title Weight Multiplier: {config.title_weight_multiplier}x
                <HelpTooltip title="How much to boost search scores when titles match the query (1.0x = no boost, 10.0x = maximum boost)" />
              </Typography>
              <Slider
                value={config.title_weight_multiplier}
                onChange={(_, value) =>
                  handleConfigChange("title_weight_multiplier", value)
                }
                min={1.0}
                max={10.0}
                step={0.5}
                marks={[
                  { value: 1.0, label: "1.0x" },
                  { value: 3.0, label: "3.0x" },
                  { value: 5.0, label: "5.0x" },
                  { value: 10.0, label: "10.0x" },
                ]}
                disabled={!config.title_weighting_enabled}
                sx={{ mt: 1 }}
              />
            </Grid>
          </Grid>

          <Divider sx={{ my: 3 }} />

          {/* Full Document Escalation */}
          <Typography variant="h6" gutterBottom>
            Full Document Escalation
            <HelpTooltip title="Adds full text of top‑matched docs to the prompt (capped if larger than token limit). Escalated docs won’t also include chunks; other docs still add chunk context. May approach model context limits." />
          </Typography>

          <Grid container spacing={3} sx={{ mb: 3 }}>
            <Grid item xs={12} sm={6}>
              <FormControlLabel
                control={
                  <Switch
                    checked={config.fetch_full_documents}
                    onChange={(e) =>
                      handleConfigChange(
                        "fetch_full_documents",
                        e.target.checked,
                      )
                    }
                  />
                }
                label={
                  <Box>
                    <Typography variant="body1">
                      Enable Full Document Fetch
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Allow including full documents when needed
                    </Typography>
                  </Box>
                }
              />
            </Grid>

            <Grid item xs={12} sm={3}>
              <TextField
                fullWidth
                label="Max Docs"
                type="number"
                value={config.full_doc_max_docs}
                onChange={(e) =>
                  handleConfigChange(
                    "full_doc_max_docs",
                    parseInt(e.target.value) || 1,
                  )
                }
                inputProps={{ min: 1, max: 10 }}
                helperText="Maximum full documents to include"
                disabled={!config.fetch_full_documents}
              />
            </Grid>

            <Grid item xs={12} sm={3}>
              <TextField
                fullWidth
                label="Token Cap"
                type="number"
                value={config.full_doc_token_cap}
                onChange={(e) =>
                  handleConfigChange(
                    "full_doc_token_cap",
                    parseInt(e.target.value) || 8000,
                  )
                }
                inputProps={{ min: 1000, max: 200000, step: 1000 }}
                helperText="Max tokens across full-doc content"
                disabled={!config.fetch_full_documents}
              />
            </Grid>
          </Grid>

          <Divider sx={{ my: 3 }} />

          {/* Context Formatting */}
          <Typography variant="h6" gutterBottom>
            Context Formatting
            <HelpTooltip title="Configure how retrieved content is formatted and presented to the AI model" />
          </Typography>

          <Grid container spacing={3} sx={{ mb: 3 }}>
            <Grid item xs={12} sm={6}>
              <FormControl component="fieldset">
                <FormLabel component="legend">
                  Context Detail Level
                  <HelpTooltip title="'Detailed' includes metadata and source info. 'Simple' provides just the content text." />
                </FormLabel>
                <RadioGroup
                  value={config.context_format}
                  onChange={(e) =>
                    handleConfigChange("context_format", e.target.value)
                  }
                >
                  <FormControlLabel
                    value="detailed"
                    control={<Radio />}
                    label="Detailed (with metadata)"
                  />
                  <FormControlLabel
                    value="simple"
                    control={<Radio />}
                    label="Simple (content only)"
                  />
                </RadioGroup>
              </FormControl>
            </Grid>

            <Grid item xs={12} sm={6}>
              <FormControl component="fieldset">
                <FormLabel component="legend">
                  Reference Format
                  <HelpTooltip title="How citations and references should be formatted in responses" />
                </FormLabel>
                <RadioGroup
                  value={config.reference_format}
                  onChange={(e) =>
                    handleConfigChange("reference_format", e.target.value)
                  }
                >
                  <FormControlLabel
                    value="markdown"
                    control={<Radio />}
                    label="Markdown links"
                  />
                  <FormControlLabel
                    value="text"
                    control={<Radio />}
                    label="Plain text"
                  />
                </RadioGroup>
              </FormControl>
            </Grid>
          </Grid>

          <Box sx={{ mb: 3 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={config.include_references}
                  onChange={(e) =>
                    handleConfigChange("include_references", e.target.checked)
                  }
                />
              }
              label={
                <Box display="flex" alignItems="center">
                  Include References in Responses
                  <HelpTooltip title="Whether to include source citations and references in AI responses" />
                </Box>
              }
            />
          </Box>
        </Box>
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          onClick={handleSave}
          variant="contained"
          startIcon={<SaveIcon />}
          disabled={!hasChanges || updateConfigMutation.isLoading}
        >
          {updateConfigMutation.isLoading ? "Saving..." : "Save Configuration"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default KBConfigDialog;
