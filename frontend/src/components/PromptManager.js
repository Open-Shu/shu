/**
 * Generalized Prompt Manager Component
 *
 * This component provides a comprehensive interface for managing prompts
 * across different entity types (knowledge bases, LLM models, agents, etc.).
 */

import React, { useState } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  Grid,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  TextField,
  Typography,
  Switch,
  FormControlLabel,
  Tooltip,
  Alert,
} from '@mui/material';
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  Assignment as AssignmentIcon,
  Visibility as ActiveIcon,
  VisibilityOff as InactiveIcon,
  Search as SearchIcon,
  Refresh as RefreshIcon,
  Preview as PreviewIcon,
  ContentCopy as CopyIcon,
} from '@mui/icons-material';
import { useQuery, useMutation, useQueryClient } from 'react-query';

import { promptAPI, ENTITY_TYPES } from '../api/prompts';
import { extractItemsFromResponse } from '../services/api';
import { log } from '../utils/log';

const ENTITY_TYPE_LABELS = {
  [ENTITY_TYPES.KNOWLEDGE_BASE]: 'Knowledge Base',
  [ENTITY_TYPES.LLM_MODEL]: 'LLM Model',
  [ENTITY_TYPES.AGENT]: 'Agent',
  [ENTITY_TYPES.WORKFLOW]: 'Workflow',
  [ENTITY_TYPES.TOOL]: 'Tool',
};

function PromptManager({
  knowledgeBaseId = null, // Legacy prop for backward compatibility
  entityType = null,
  entityId = null,
  title = 'Prompt Management',
  showEntityFilter = true,
  onPromptSelect = null,
  open = true,
  onClose = null,
}) {
  // Handle legacy knowledgeBaseId prop
  const actualEntityType = entityType || (knowledgeBaseId ? ENTITY_TYPES.KNOWLEDGE_BASE : null);
  const actualEntityId = entityId || knowledgeBaseId;

  const [selectedPrompt, setSelectedPrompt] = useState(null);
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
  const [isPreviewDialogOpen, setIsPreviewDialogOpen] = useState(false);
  const [previewPrompt, setPreviewPrompt] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterEntityType, setFilterEntityType] = useState(actualEntityType || '');
  const [filterActive, setFilterActive] = useState(null);

  const [formData, setFormData] = useState({
    name: '',
    description: '',
    content: '',
    entity_type: actualEntityType || ENTITY_TYPES.KNOWLEDGE_BASE,
    is_active: true,
  });

  const queryClient = useQueryClient();

  // Fetch prompts with filtering
  const {
    data: promptsResponse,
    isLoading: promptsLoading,
    error: promptsError,
  } = useQuery(
    [
      'prompts',
      {
        entityType: filterEntityType,
        entityId: actualEntityId,
        search: searchTerm,
        is_active: filterActive,
      },
    ],
    () =>
      promptAPI.list({
        entity_type: filterEntityType || undefined,
        entity_id: actualEntityId || undefined,
        search: searchTerm || undefined,
        is_active: filterActive !== null ? filterActive : undefined,
        limit: 100,
      }),
    {
      refetchOnWindowFocus: false,
      staleTime: 5000, // 5 seconds - shorter for better UX
      enabled: open,
    }
  );

  const prompts = promptsResponse ? extractItemsFromResponse(promptsResponse) : [];

  // Simple debug log
  if (promptsResponse) {
    log.debug('PromptManager: Got response, extracted', prompts.length, 'prompts');
  }

  // Create prompt mutation
  const createMutation = useMutation((data) => promptAPI.create(data), {
    onSuccess: () => {
      // Invalidate all prompt queries regardless of filters
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
      setIsCreateDialogOpen(false);
      resetForm();
    },
    onError: (error) => {
      log.error('Error creating prompt:', error);
    },
  });

  // Update prompt mutation
  const updateMutation = useMutation(({ promptId, data }) => promptAPI.update(promptId, data), {
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
      setIsEditDialogOpen(false);
      setSelectedPrompt(null);
      resetForm();
    },
    onError: (error) => {
      log.error('Error updating prompt:', error);
    },
  });

  // Delete prompt mutation
  const deleteMutation = useMutation((promptId) => promptAPI.delete(promptId), {
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
      setSelectedPrompt(null);
    },
    onError: (error) => {
      log.error('Error deleting prompt:', error);
    },
  });

  const resetForm = () => {
    setFormData({
      name: '',
      description: '',
      content: '',
      entity_type: actualEntityType || ENTITY_TYPES.KNOWLEDGE_BASE,
      is_active: true,
    });
  };

  const handleCreatePrompt = () => {
    createMutation.mutate(formData);
  };

  const handleUpdatePrompt = () => {
    if (selectedPrompt) {
      updateMutation.mutate({
        promptId: selectedPrompt.id,
        data: formData,
      });
    }
  };

  const handleDeletePrompt = (promptId) => {
    if (window.confirm('Are you sure you want to delete this prompt? This will also remove all assignments.')) {
      deleteMutation.mutate(promptId);
    }
  };

  const handleEditPrompt = (prompt) => {
    setSelectedPrompt(prompt);
    setFormData({
      name: prompt.name || '',
      description: prompt.description || '',
      content: prompt.content || '',
      entity_type: prompt.entity_type || ENTITY_TYPES.KNOWLEDGE_BASE,
      is_active: prompt.is_active !== undefined ? prompt.is_active : true,
    });
    setIsEditDialogOpen(true);
  };

  const handlePromptClick = (prompt) => {
    if (onPromptSelect) {
      onPromptSelect(prompt);
    } else {
      setSelectedPrompt(prompt);
    }
  };

  const handlePreviewPrompt = (prompt) => {
    setPreviewPrompt(prompt);
    setIsPreviewDialogOpen(true);
  };

  const handleCopyPrompt = async (content) => {
    try {
      await navigator.clipboard.writeText(content);
      // Could add a toast notification here
    } catch (err) {
      log.error('Failed to copy prompt content:', err);
    }
  };

  if (!open) {
    return null;
  }

  if (promptsError) {
    return <Alert severity="error">Error loading prompts: {promptsError.message}</Alert>;
  }

  return (
    <Box>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
        <Typography variant="h5">{title}</Typography>
        <Box display="flex" gap={1}>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={() => queryClient.invalidateQueries({ queryKey: ['prompts'] })}
            disabled={promptsLoading}
          >
            Refresh
          </Button>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setIsCreateDialogOpen(true)}>
            Create Prompt
          </Button>
        </Box>
      </Box>

      {/* Filters */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Grid container spacing={2} alignItems="center">
          <Grid item xs={12} sm={4}>
            <TextField
              fullWidth
              label="Search prompts"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              InputProps={{
                startAdornment: <SearchIcon sx={{ mr: 1, color: 'text.secondary' }} />,
              }}
            />
          </Grid>
          {showEntityFilter && (
            <Grid item xs={12} sm={3}>
              <FormControl fullWidth>
                <InputLabel>Entity Type</InputLabel>
                <Select
                  value={filterEntityType}
                  onChange={(e) => setFilterEntityType(e.target.value)}
                  label="Entity Type"
                >
                  <MenuItem value="">All Types</MenuItem>
                  {Object.entries(ENTITY_TYPE_LABELS || {}).map(([value, label]) => (
                    <MenuItem key={value} value={value}>
                      {label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
          )}
          <Grid item xs={12} sm={3}>
            <FormControl fullWidth>
              <InputLabel>Status</InputLabel>
              <Select
                value={filterActive === null ? '' : filterActive.toString()}
                onChange={(e) => setFilterActive(e.target.value === '' ? null : e.target.value === 'true')}
                label="Status"
              >
                <MenuItem value="">All</MenuItem>
                <MenuItem value="true">Active</MenuItem>
                <MenuItem value="false">Inactive</MenuItem>
              </Select>
            </FormControl>
          </Grid>
        </Grid>
      </Paper>

      {/* Prompts List */}
      {promptsLoading ? (
        <Typography>Loading prompts...</Typography>
      ) : !prompts || prompts.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <Typography variant="h6" color="text.secondary" gutterBottom>
            No prompts found
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {searchTerm || filterEntityType || filterActive !== null
              ? 'Try adjusting your filters or search terms'
              : 'Create your first prompt to get started'}
          </Typography>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setIsCreateDialogOpen(true)}>
            Create Prompt
          </Button>
        </Paper>
      ) : (
        <Grid container spacing={2}>
          {prompts &&
            prompts
              .filter((prompt) => prompt && prompt.id)
              .map((prompt) => (
                <Grid item xs={12} md={6} lg={4} key={prompt.id}>
                  <Card
                    sx={{
                      cursor: onPromptSelect ? 'pointer' : 'default',
                      '&:hover': onPromptSelect ? { boxShadow: 3 } : {},
                    }}
                    onClick={() => handlePromptClick(prompt)}
                  >
                    <CardContent>
                      <Box display="flex" justifyContent="space-between" alignItems="flex-start" mb={2}>
                        <Box flex={1}>
                          <Typography variant="h6" gutterBottom>
                            {prompt.name || 'Untitled Prompt'}
                          </Typography>
                          <Box display="flex" gap={1} mb={1}>
                            <Chip
                              label={ENTITY_TYPE_LABELS[prompt.entity_type] || prompt.entity_type || 'Unknown'}
                              size="small"
                              variant="outlined"
                            />
                            {prompt.is_active ? (
                              <Chip label="Active" size="small" color="success" icon={<ActiveIcon />} />
                            ) : (
                              <Chip label="Inactive" size="small" color="default" icon={<InactiveIcon />} />
                            )}
                            {prompt.is_system_default && (
                              <Tooltip title="System Default (Protected - Cannot be edited or deleted)">
                                <Chip
                                  label="🔒 System"
                                  size="small"
                                  color="warning"
                                  variant="filled"
                                  sx={{
                                    backgroundColor: '#ff9800',
                                    color: 'white',
                                    fontWeight: 'bold',
                                  }}
                                />
                              </Tooltip>
                            )}
                          </Box>
                        </Box>
                        {!onPromptSelect && (
                          <Box>
                            {!prompt.is_system_default && (
                              <>
                                <Tooltip title="Edit">
                                  <IconButton
                                    size="small"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleEditPrompt(prompt);
                                    }}
                                  >
                                    <EditIcon />
                                  </IconButton>
                                </Tooltip>
                                <Tooltip title="Delete">
                                  <IconButton
                                    size="small"
                                    color="error"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleDeletePrompt(prompt.id);
                                    }}
                                  >
                                    <DeleteIcon />
                                  </IconButton>
                                </Tooltip>
                              </>
                            )}
                          </Box>
                        )}
                      </Box>

                      {prompt.description && (
                        <Typography variant="body2" color="text.secondary" gutterBottom>
                          {prompt.description}
                        </Typography>
                      )}

                      <Typography
                        variant="body2"
                        sx={{
                          mt: 1,
                          fontFamily: 'monospace',
                          backgroundColor: 'grey.100',
                          p: 1,
                          borderRadius: 1,
                          maxHeight: 100,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                      >
                        {prompt.content && prompt.content.length > 150
                          ? `${prompt.content.substring(0, 150)}...`
                          : prompt.content || 'No content'}
                      </Typography>

                      <Box display="flex" justifyContent="space-between" alignItems="center" mt={2}>
                        <Typography variant="caption" color="text.secondary">
                          Version {prompt.version || 1}
                        </Typography>
                        <Box display="flex" alignItems="center" gap={1}>
                          {prompt.assigned_entity_ids && prompt.assigned_entity_ids.length > 0 && (
                            <Chip
                              label={`${prompt.assigned_entity_ids.length} assignments`}
                              size="small"
                              icon={<AssignmentIcon />}
                              variant="outlined"
                            />
                          )}
                          <Tooltip title="Preview Full Content">
                            <IconButton
                              size="small"
                              onClick={(e) => {
                                e.stopPropagation();
                                handlePreviewPrompt(prompt);
                              }}
                            >
                              <PreviewIcon />
                            </IconButton>
                          </Tooltip>
                        </Box>
                      </Box>
                    </CardContent>
                  </Card>
                </Grid>
              ))}
        </Grid>
      )}

      {/* Create Dialog */}
      <Dialog open={isCreateDialogOpen} onClose={() => setIsCreateDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Create New Prompt</DialogTitle>
        <DialogContent>
          <Grid container spacing={2} sx={{ mt: 1 }}>
            <Grid item xs={12} sm={8}>
              <TextField
                fullWidth
                label="Prompt Name"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                required
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <FormControl fullWidth>
                <InputLabel>Entity Type</InputLabel>
                <Select
                  value={formData.entity_type}
                  onChange={(e) => setFormData({ ...formData, entity_type: e.target.value })}
                  label="Entity Type"
                  disabled={!!actualEntityType} // Disable if entityType is fixed
                >
                  {Object.entries(ENTITY_TYPE_LABELS || {}).map(([value, label]) => (
                    <MenuItem key={value} value={value}>
                      {label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Description"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                multiline
                rows={2}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Prompt Content"
                value={formData.content}
                onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                multiline
                rows={8}
                required
                helperText="Enter the prompt template or instructions"
              />
            </Grid>
            <Grid item xs={12}>
              <FormControlLabel
                control={
                  <Switch
                    checked={formData.is_active}
                    onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                  />
                }
                label="Active"
              />
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setIsCreateDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleCreatePrompt}
            variant="contained"
            disabled={!formData.name || !formData.content || createMutation.isLoading}
          >
            {createMutation.isLoading ? 'Creating...' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Edit Dialog */}
      <Dialog open={isEditDialogOpen} onClose={() => setIsEditDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Edit Prompt</DialogTitle>
        <DialogContent>
          <Grid container spacing={2} sx={{ mt: 1 }}>
            <Grid item xs={12} sm={8}>
              <TextField
                fullWidth
                label="Prompt Name"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                required
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <FormControl fullWidth>
                <InputLabel>Entity Type</InputLabel>
                <Select
                  value={formData.entity_type}
                  onChange={(e) => setFormData({ ...formData, entity_type: e.target.value })}
                  label="Entity Type"
                  disabled // Entity type cannot be changed after creation
                >
                  {Object.entries(ENTITY_TYPE_LABELS || {}).map(([value, label]) => (
                    <MenuItem key={value} value={value}>
                      {label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Description"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                multiline
                rows={2}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Prompt Content"
                value={formData.content}
                onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                multiline
                rows={8}
                required
                helperText="Enter the prompt template or instructions"
              />
            </Grid>
            <Grid item xs={12}>
              <FormControlLabel
                control={
                  <Switch
                    checked={formData.is_active}
                    onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                  />
                }
                label="Active"
              />
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setIsEditDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleUpdatePrompt}
            variant="contained"
            disabled={!formData.name || !formData.content || updateMutation.isLoading}
          >
            {updateMutation.isLoading ? 'Updating...' : 'Update'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Preview Dialog */}
      <Dialog open={isPreviewDialogOpen} onClose={() => setIsPreviewDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>
          <Box display="flex" justifyContent="space-between" alignItems="center">
            <Typography variant="h6">{previewPrompt?.name || 'Prompt Preview'}</Typography>
            <Box display="flex" gap={1}>
              <Chip
                label={ENTITY_TYPE_LABELS[previewPrompt?.entity_type] || previewPrompt?.entity_type || 'Unknown'}
                size="small"
                variant="outlined"
              />
              {previewPrompt?.is_active ? (
                <Chip label="Active" size="small" color="success" icon={<ActiveIcon />} />
              ) : (
                <Chip label="Inactive" size="small" color="default" icon={<InactiveIcon />} />
              )}
              {previewPrompt?.is_system_default && (
                <Chip label="🔒 System" size="small" color="warning" variant="filled" />
              )}
            </Box>
          </Box>
        </DialogTitle>
        <DialogContent>
          {previewPrompt?.description && (
            <Alert severity="info" sx={{ mb: 2 }}>
              <Typography variant="body2">
                <strong>Description:</strong> {previewPrompt.description}
              </Typography>
            </Alert>
          )}

          {/* Citation Handling Notice */}
          {previewPrompt?.entity_type === 'knowledge_base' &&
            previewPrompt?.content &&
            (previewPrompt.content.toLowerCase().includes('citation') ||
              previewPrompt.content.toLowerCase().includes('reference') ||
              previewPrompt.content.toLowerCase().includes('source')) && (
              <Alert severity="info" sx={{ mb: 2 }}>
                <Typography variant="body2">
                  <strong>Citation Handling:</strong> This prompt includes citation instructions. When used,
                  system-level references will be automatically disabled to prevent duplication. The prompt will handle
                  citations directly in the response.
                </Typography>
              </Alert>
            )}

          <Typography variant="subtitle2" gutterBottom>
            Prompt Content:
          </Typography>
          <Paper
            sx={{
              p: 2,
              backgroundColor: 'grey.50',
              border: '1px solid',
              borderColor: 'grey.200',
              borderRadius: 1,
              maxHeight: 400,
              overflow: 'auto',
            }}
          >
            <Typography
              variant="body2"
              component="pre"
              sx={{
                fontFamily: 'monospace',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                margin: 0,
              }}
            >
              {previewPrompt?.content || 'No content available'}
            </Typography>
          </Paper>
        </DialogContent>
        <DialogActions>
          <Button startIcon={<CopyIcon />} onClick={() => handleCopyPrompt(previewPrompt?.content || '')}>
            Copy Content
          </Button>
          <Button onClick={() => setIsPreviewDialogOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

export default PromptManager;
