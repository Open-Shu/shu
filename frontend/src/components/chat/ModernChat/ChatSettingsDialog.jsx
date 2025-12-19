import React from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  Grid,
  InputLabel,
  MenuItem,
  Select,
  Slider,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import { RAG_REWRITE_OPTIONS } from '../../../utils/constants';
import NotImplemented from '../../NotImplemented';
import ModelConfigSelector from './ModelConfigSelector';

const ChatSettingsDialog = React.memo(function ChatSettingsDialog({
  open,
  onClose,
  userPreferences,
  onUserPreferencesChange,
  automationSettings,
  onAutomationSettingsChange,
  onSave,
  isSaving,
  ragRewriteMode,
  setRagRewriteMode,
  // Model configuration props (for mobile users)
  availableModelConfigs,
  selectedModelConfig,
  onModelChange,
  disableModelSelect,
}) {

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Chat Settings</DialogTitle>
      <DialogContent>
        <Box sx={{ mt: 2 }}>
          {/* Model Configuration - visible for mobile users */}
          {Array.isArray(availableModelConfigs) && availableModelConfigs.length > 0 && (
            <>
              <Typography variant="h6" gutterBottom>
                Model Configuration
              </Typography>
              <Grid container spacing={3} sx={{ mb: 3 }}>
                <Grid item xs={12}>
                  <ModelConfigSelector
                    availableModelConfigs={availableModelConfigs}
                    selectedModelConfig={selectedModelConfig}
                    onModelChange={onModelChange}
                    disabled={disableModelSelect}
                    fullWidth
                  />
                </Grid>
              </Grid>
            </>
          )}

          <Typography variant="h6" gutterBottom>
            Memory Settings
          </Typography>
          <Grid container spacing={3} sx={{ mb: 3 }}>
            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                label="Memory Depth"
                type="number"
                value={userPreferences.memory_depth}
                onChange={(e) =>
                  onUserPreferencesChange({
                    memory_depth: parseInt(e.target.value, 10) || 5,
                  })
                }
                inputProps={{ min: 1, max: 20 }}
                helperText="Number of previous conversations to consider"
              />
              <Typography variant="caption" color="text.secondary">
                Shu trims context to roughly this many prior messages before each response.
              </Typography>
            </Grid>
            <Grid item xs={12} sm={6}>
              <Typography gutterBottom>
                Memory Similarity Threshold: {userPreferences.memory_similarity_threshold}
              </Typography>
              <Slider
                value={userPreferences.memory_similarity_threshold}
                onChange={(_, value) =>
                  onUserPreferencesChange({
                    memory_similarity_threshold: value,
                  })
                }
                min={0}
                max={1}
                step={0.1}
                marks={[
                  { value: 0, label: '0' },
                  { value: 0.5, label: '0.5' },
                  { value: 1, label: '1' },
                ]}
              />
              <Box sx={{ mt: 0.5 }}>
                <NotImplemented label="Not used by backend memory system yet" />
              </Box>
            </Grid>
          </Grid>

          <Typography variant="h6" gutterBottom>Retrieval Strategy</Typography>
          <Grid container spacing={3} sx={{ mb: 3 }}>
            <Grid item xs={12}>
              <FormControl fullWidth>
                <InputLabel
                  id="chat-rag-mode-label"
                  sx={{
                    backgroundColor: 'background.paper',
                    px: 0.5,
                    '&.Mui-focused': {
                      backgroundColor: 'background.paper',
                    }
                  }}
                >
                  RAG Query Mode
                </InputLabel>
                <Select
                  labelId="chat-rag-mode-label"
                  id="chat-rag-mode"
                  value={ragRewriteMode}
                  label="RAG Query Mode"
                  onChange={(e) => setRagRewriteMode(e.target.value)}
                >
                  {RAG_REWRITE_OPTIONS.map(option => (
                    <MenuItem key={option.value} value={option.value}>
                      {option.label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                Choose how Shu prepares knowledge base queries: disable RAG entirely, pass the raw message,
                distill it to critical terms, or let the side-call rewrite it for retrieval.
              </Typography>
            </Grid>
          </Grid>

          <Typography variant="h6" gutterBottom>
            Automation
          </Typography>
          <Grid container spacing={3} sx={{ mb: 3 }}>
            <Grid item xs={12}>
              <FormControlLabel
                control={
                  <Switch
                    checked={automationSettings.firstUserRename}
                    onChange={(e) =>
                      onAutomationSettingsChange({
                        firstUserRename: e.target.checked,
                      })
                    }
                  />
                }
                label="Auto-rename on first user message"
              />
              <Typography variant="body2" color="text.secondary">
                Renames immediately after you send the very first message (if unlocked).
              </Typography>
            </Grid>
            <Grid item xs={12} sm={6}>
              <FormControlLabel
                control={
                  <Switch
                    checked={automationSettings.firstAssistantSummary}
                    onChange={(e) =>
                      onAutomationSettingsChange({
                        firstAssistantSummary: e.target.checked,
                      })
                    }
                  />
                }
                label="Run summary on first assistant reply"
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                label="Subsequent cadence (every N assistant replies)"
                type="number"
                value={automationSettings.cadenceInterval}
                onChange={(e) =>
                  onAutomationSettingsChange({
                    cadenceInterval: Math.max(
                      0,
                      parseInt(e.target.value || '0', 10)
                    ),
                  })
                }
                inputProps={{ min: 0 }}
                helperText="0 disables recurring cadence beyond the second reply"
              />
            </Grid>
          </Grid>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={onSave} variant="contained" disabled={isSaving}>
          {isSaving ? 'Saving...' : 'Save Settings'}
        </Button>
      </DialogActions>
    </Dialog>
  );
});

export default ChatSettingsDialog;
