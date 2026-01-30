import React from "react";
import { useQuery } from "react-query";
import {
  Box,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  FormControlLabel,
  Switch,
  Slider,
  Typography,
  Divider,
} from "@mui/material";
import { knowledgeBaseAPI, extractDataFromResponse } from "../services/api";

/**
 * Reusable QueryConfiguration component for both QueryTester and LLMTester
 * Follows DRY principle by centralizing query configuration controls
 */
function QueryConfiguration({
  // Required props
  selectedKB,
  onKBChange,
  queryText,
  onQueryTextChange,

  // Optional props with defaults
  searchType = "hybrid",
  onSearchTypeChange,
  limit = 10,
  onLimitChange,
  threshold = null,
  onThresholdChange,
  titleWeightingEnabled = true,
  onTitleWeightingEnabledChange,
  titleWeightMultiplier = 3.0,
  onTitleWeightMultiplierChange,

  // UI customization
  showKBSelector = true,
  showSearchType = true,
  showQueryText = true,
  showLimit = true,
  showThreshold = true,
  showTitleWeighting = true,
  queryTextLabel = "Query Text",
  queryTextPlaceholder = "Enter your search query...",
  queryTextRows = 4,

  // Loading states
  kbLoading = false,
  knowledgeBases = [],
}) {
  // Fetch KB config when selectedKB changes to get default threshold and title weighting
  useQuery(
    ["kb-config", selectedKB],
    () => (selectedKB ? knowledgeBaseAPI.getRAGConfig(selectedKB) : null),
    {
      enabled: !!selectedKB,
      onSuccess: (data) => {
        if (data && threshold === null) {
          const config = extractDataFromResponse(data);
          if (onThresholdChange) {
            onThresholdChange(config.search_threshold || 0.7);
          }
          if (onTitleWeightingEnabledChange) {
            onTitleWeightingEnabledChange(
              config.title_weighting_enabled ?? true,
            );
          }
          if (onTitleWeightMultiplierChange) {
            onTitleWeightMultiplierChange(
              config.title_weight_multiplier || 3.0,
            );
          }
        }
      },
    },
  );

  return (
    <Box>
      {showKBSelector && (
        <FormControl fullWidth sx={{ mb: 2 }}>
          <InputLabel
            sx={{
              backgroundColor: "background.paper",
              px: 0.5,
              "&.Mui-focused": {
                backgroundColor: "background.paper",
              },
            }}
          >
            Knowledge Base
          </InputLabel>
          <Select
            value={selectedKB}
            onChange={(e) => onKBChange && onKBChange(e.target.value)}
            disabled={kbLoading}
          >
            {knowledgeBases?.map((kb) => (
              <MenuItem key={kb.id} value={kb.id}>
                {kb.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}

      {showSearchType && (
        <FormControl fullWidth sx={{ mb: 2 }}>
          <InputLabel
            sx={{
              backgroundColor: "background.paper",
              px: 0.5,
              "&.Mui-focused": {
                backgroundColor: "background.paper",
              },
            }}
          >
            Search Type
          </InputLabel>
          <Select
            value={searchType}
            onChange={(e) =>
              onSearchTypeChange && onSearchTypeChange(e.target.value)
            }
          >
            <MenuItem value="similarity">Similarity Search</MenuItem>
            <MenuItem value="keyword">Keyword Search</MenuItem>
            <MenuItem value="hybrid">Hybrid Search</MenuItem>
          </Select>
        </FormControl>
      )}

      {showQueryText && (
        <TextField
          fullWidth
          label={queryTextLabel}
          multiline
          rows={queryTextRows}
          value={queryText}
          onChange={(e) =>
            onQueryTextChange && onQueryTextChange(e.target.value)
          }
          placeholder={queryTextPlaceholder}
          sx={{
            mb: 2,
            "& .MuiInputBase-input::placeholder": {
              color: "#9ca3af",
              opacity: 0.7,
              fontStyle: "italic",
            },
          }}
        />
      )}

      {showLimit && (
        <TextField
          fullWidth
          label="Limit"
          type="number"
          value={limit}
          onChange={(e) => onLimitChange && onLimitChange(e.target.value)}
          placeholder="Limit (e.g., 10)"
          sx={{ mb: 2 }}
          inputProps={{ min: 1, max: 100 }}
          helperText="Maximum number of results to return"
        />
      )}

      {showThreshold &&
        (searchType === "similarity" ||
          searchType === "keyword" ||
          searchType === "hybrid") && (
          <TextField
            fullWidth
            label="Threshold"
            type="number"
            value={threshold || ""}
            onChange={(e) =>
              onThresholdChange && onThresholdChange(e.target.value)
            }
            placeholder="Threshold (e.g., 0.7)"
            sx={{ mb: 2 }}
            inputProps={{ min: 0, max: 1, step: 0.1 }}
            helperText={
              searchType === "similarity"
                ? "Similarity threshold (0.0 - 1.0)"
                : searchType === "keyword"
                  ? "Score threshold (0.0 - 1.0)"
                  : "Score threshold (0.0 - 1.0)"
            }
          />
        )}

      {/* Title Weighting Controls */}
      {showTitleWeighting &&
        (searchType === "keyword" || searchType === "hybrid") && (
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
                  onChange={(e) =>
                    onTitleWeightingEnabledChange &&
                    onTitleWeightingEnabledChange(e.target.checked)
                  }
                  color="primary"
                />
              }
              label="Enable Title Weighting"
              sx={{ mb: 2, display: "block" }}
            />

            {titleWeightingEnabled && (
              <Box sx={{ px: 1 }}>
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  Title Weight Multiplier: {titleWeightMultiplier}x
                </Typography>
                <Slider
                  value={titleWeightMultiplier}
                  onChange={(_, newValue) =>
                    onTitleWeightMultiplierChange &&
                    onTitleWeightMultiplierChange(newValue)
                  }
                  min={1.0}
                  max={10.0}
                  step={0.5}
                  marks={[
                    { value: 1.0, label: "1x" },
                    { value: 3.0, label: "3x" },
                    { value: 5.0, label: "5x" },
                    { value: 10.0, label: "10x" },
                  ]}
                  valueLabelDisplay="auto"
                  sx={{ mb: 1 }}
                />
                <Typography variant="caption" color="text.secondary">
                  Higher values boost documents with matching titles more
                  strongly
                </Typography>
              </Box>
            )}
          </Box>
        )}
    </Box>
  );
}

export default QueryConfiguration;
