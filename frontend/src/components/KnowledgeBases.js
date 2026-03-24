import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
  Box,
  Typography,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  IconButton,
  Chip,
  Alert,
  AlertTitle,
  CircularProgress,
  LinearProgress,
  Tooltip,
} from '@mui/material';
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  Visibility as ViewIcon,
  Settings as ConfigIcon,
  Description as DocumentsIcon,
  RssFeed as FeedsIcon,
  Storage as KBIcon,
  Refresh as RefreshIcon,
  Upload as UploadIcon,
} from '@mui/icons-material';
import PageHelpHeader from './PageHelpHeader';
import ExportKBButton from './ExportKBButton';
import ImportKBWizard from './ImportKBWizard';

import { knowledgeBaseAPI, formatError, extractItemsFromResponse } from '../services/api';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { keyframes } from '@mui/system';

import KBConfigDialog from './KBConfigDialog';
import ReEmbedConfirmDialog from './ReEmbedConfirmDialog';
import JSONPretty from 'react-json-pretty';
import 'react-json-pretty/themes/monikai.css';

// Pulsing animation for highlighting the documents button
const pulseAnimation = keyframes`
  0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(25, 118, 210, 0.4); }
  50% { transform: scale(1.15); box-shadow: 0 0 0 8px rgba(25, 118, 210, 0); }
  100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(25, 118, 210, 0); }
`;

function KnowledgeBases() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const highlightDocs = searchParams.get('action') === 'add-documents';

  const [selectedKB, setSelectedKB] = useState(null);
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
  const [isViewDialogOpen, setIsViewDialogOpen] = useState(false);

  const [isConfigDialogOpen, setIsConfigDialogOpen] = useState(false);
  const [selectedKBForManagement, setSelectedKBForManagement] = useState(null);
  const [formData, setFormData] = useState({
    name: '',
    description: '',
  });

  const queryClient = useQueryClient();

  const [reEmbedTarget, setReEmbedTarget] = useState(null);
  const [isImportWizardOpen, setIsImportWizardOpen] = useState(false);

  const {
    data: knowledgeBasesResponse,
    isLoading,
    error,
    refetch,
  } = useQuery('knowledgeBases', knowledgeBaseAPI.list, {
    refetchInterval: (data) => {
      const items = extractItemsFromResponse(data);
      return items?.some((kb) => kb.embedding_status === 're_embedding' || kb.status === 'importing') ? 5000 : false;
    },
  });

  // Extract data from envelope format
  const knowledgeBases = extractItemsFromResponse(knowledgeBasesResponse);

  const createMutation = useMutation(knowledgeBaseAPI.create, {
    onSuccess: () => {
      queryClient.invalidateQueries('knowledgeBases');
      setIsCreateDialogOpen(false);
      setFormData({
        name: '',
        description: '',
      });
    },
  });

  const updateMutation = useMutation(({ id, data }) => knowledgeBaseAPI.update(id, data), {
    onSuccess: () => {
      queryClient.invalidateQueries('knowledgeBases');
      setIsEditDialogOpen(false);
    },
  });

  const deleteMutation = useMutation(knowledgeBaseAPI.delete, {
    onSuccess: () => {
      queryClient.invalidateQueries('knowledgeBases');
    },
  });

  const reEmbedMutation = useMutation((id) => knowledgeBaseAPI.triggerReEmbed(id), {
    onSuccess: () => {
      queryClient.invalidateQueries('knowledgeBases');
      setReEmbedTarget(null);
    },
  });

  const handleCreate = () => {
    createMutation.mutate(formData);
  };

  const handleUpdate = () => {
    updateMutation.mutate({ id: selectedKB.id, data: formData });
  };

  const handleDelete = (id) => {
    if (window.confirm('Are you sure you want to delete this knowledge base?')) {
      deleteMutation.mutate(id);
    }
  };

  const handleEdit = (kb) => {
    setSelectedKB(kb);
    setFormData({
      name: kb.name,
      description: kb.description,
    });
    setIsEditDialogOpen(true);
  };

  const handleView = (kb) => {
    setSelectedKB(kb);
    setIsViewDialogOpen(true);
  };

  const handleConfigureKB = (kb) => {
    setSelectedKBForManagement(kb);
    setIsConfigDialogOpen(true);
  };

  const handleViewDocuments = (kb) => {
    navigate(`/admin/knowledge-bases/${kb.id}/documents`);
  };
  const handleViewFeeds = (kb) => {
    navigate(`/admin/knowledge-bases/${kb.id}/documents?tab=feeds`);
  };

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error">Error loading knowledge bases: {formatError(error).message}</Alert>;
  }

  return (
    <Box>
      <PageHelpHeader
        title="Knowledge Bases"
        description="Knowledge Bases are searchable collections of documents that power RAG (Retrieval-Augmented Generation). Create a KB, then add documents manually or configure Plugin Feeds to sync data automatically from external sources."
        icon={<KBIcon />}
        tips={[
          'Create a KB first, then click "Docs" to upload documents or "Feeds" to set up automated ingestion',
          'Configure retrieval settings via the gear icon to tune chunk size, overlap, and search behavior',
          'Use KB Permissions (Access Control menu) to control who can access each knowledge base',
          'Documents are automatically chunked and embedded for vector search',
        ]}
      />
      {highlightDocs && (
        <Alert severity="info" sx={{ mb: 2 }} onClose={() => setSearchParams({})}>
          Click the pulsing <DocumentsIcon fontSize="small" sx={{ verticalAlign: 'middle', mx: 0.5 }} /> button on any
          Knowledge Base to add documents.
        </Alert>
      )}
      {knowledgeBases?.some((kb) => ['stale', 'error', 're_embedding'].includes(kb.embedding_status)) && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          <AlertTitle>Embedding Model Changed</AlertTitle>
          One or more knowledge bases have embeddings from an outdated model. Vector search is disabled for these KBs
          until re-embedding is complete. Use the re-embed button (
          <RefreshIcon fontSize="small" sx={{ verticalAlign: 'middle', mx: 0.5 }} />) on each affected KB to update its
          embeddings. Keyword search continues working normally.
        </Alert>
      )}
      <Box display="flex" justifyContent="flex-end" alignItems="center" mb={2}>
        <Box>
          <Button variant="outlined" onClick={() => refetch()} sx={{ mr: 2 }}>
            Refresh
          </Button>
          <Button
            variant="outlined"
            startIcon={<UploadIcon />}
            onClick={() => setIsImportWizardOpen(true)}
            sx={{ mr: 1 }}
          >
            Import
          </Button>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setIsCreateDialogOpen(true)}>
            Create Knowledge Base
          </Button>
        </Box>
      </Box>

      {isLoading && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Loading knowledge bases...
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Error loading knowledge bases: {formatError(error).message}
        </Alert>
      )}
      {!isLoading && !error && (!knowledgeBases || knowledgeBases.length === 0) && (
        <Alert severity="info" sx={{ mb: 2 }}>
          No knowledge bases found. Create your first knowledge base to get started.
        </Alert>
      )}

      {knowledgeBases && knowledgeBases.length > 0 && (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Name & Description</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Embedding</TableCell>
                <TableCell>Documents</TableCell>
                <TableCell>Chunks</TableCell>
                <TableCell>Sync</TableCell>
                <TableCell>Created</TableCell>
                <TableCell>Last Sync</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {knowledgeBases.map((kb) => (
                <TableRow key={kb.id} hover>
                  <TableCell>
                    <Box>
                      <Typography variant="subtitle1" fontWeight="medium">
                        {kb.name}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {kb.description || 'No description'}
                      </Typography>
                    </Box>
                  </TableCell>
                  <TableCell>
                    {kb.status === 'importing' ? (
                      <Box>
                        <Chip label="Importing" color="info" size="small" />
                        {kb.import_progress && kb.import_progress.phase !== 'queued' && (
                          <Tooltip
                            title={`Phase: ${kb.import_progress.phase || '...'} — docs: ${kb.import_progress.documents_done || 0}/${kb.import_progress.documents_total || '?'}, chunks: ${kb.import_progress.chunks_done || 0}/${kb.import_progress.chunks_total || '?'}`}
                          >
                            <LinearProgress
                              variant="determinate"
                              value={
                                kb.import_progress.documents_total
                                  ? (((kb.import_progress.documents_done || 0) +
                                      (kb.import_progress.chunks_done || 0) +
                                      (kb.import_progress.queries_done || 0)) /
                                      ((kb.import_progress.documents_total || 1) +
                                        (kb.import_progress.chunks_total || 0) +
                                        (kb.import_progress.queries_total || 0))) *
                                    100
                                  : 0
                              }
                              sx={{ mt: 0.5, borderRadius: 1 }}
                            />
                          </Tooltip>
                        )}
                      </Box>
                    ) : (
                      <Chip label={kb.status} color={kb.status === 'active' ? 'success' : 'default'} size="small" />
                    )}
                  </TableCell>
                  <TableCell>
                    {kb.embedding_status === 're_embedding' ? (
                      <Box>
                        <Chip label="Re-embedding" color="info" size="small" />
                        {kb.re_embedding_progress && (
                          <Tooltip
                            title={`${kb.re_embedding_progress.chunks_done || 0} / ${kb.re_embedding_progress.chunks_total || '?'} chunks — phase: ${kb.re_embedding_progress.phase || 'chunks'}`}
                          >
                            <LinearProgress
                              variant="determinate"
                              value={
                                kb.re_embedding_progress.chunks_total
                                  ? (kb.re_embedding_progress.chunks_done / kb.re_embedding_progress.chunks_total) * 100
                                  : 0
                              }
                              sx={{ mt: 0.5, borderRadius: 1 }}
                            />
                          </Tooltip>
                        )}
                      </Box>
                    ) : kb.embedding_status === 'stale' ? (
                      <Chip label="Stale" color="warning" size="small" />
                    ) : kb.embedding_status === 'error' ? (
                      <Chip label="Error" color="error" size="small" />
                    ) : (
                      <Chip label="Current" color="success" size="small" />
                    )}
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{kb.document_count || 0}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{kb.total_chunks || 0}</Typography>
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={kb.sync_enabled ? 'Enabled' : 'Disabled'}
                      color={kb.sync_enabled ? 'success' : 'error'}
                      size="small"
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{new Date(kb.created_at).toLocaleDateString()}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">
                      {kb.last_sync_at ? new Date(kb.last_sync_at).toLocaleDateString() : 'Never'}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Box display="flex" gap={0.5}>
                      <IconButton size="small" onClick={() => handleView(kb)} title="View Details">
                        <ViewIcon />
                      </IconButton>
                      <IconButton
                        size="small"
                        onClick={() => {
                          // Clear the highlight param when clicking
                          if (highlightDocs) {
                            setSearchParams({});
                          }
                          handleViewDocuments(kb);
                        }}
                        title={highlightDocs ? 'Click to add documents to this Knowledge Base' : 'View Documents'}
                        color="primary"
                        sx={
                          highlightDocs
                            ? {
                                animation: `${pulseAnimation} 1.5s ease-in-out infinite`,
                                bgcolor: 'primary.light',
                                color: 'primary.contrastText',
                                '&:hover': {
                                  bgcolor: 'primary.main',
                                },
                              }
                            : {}
                        }
                      >
                        <DocumentsIcon />
                      </IconButton>
                      <IconButton size="small" onClick={() => handleEdit(kb)} title="Edit">
                        <EditIcon />
                      </IconButton>
                      <IconButton
                        size="small"
                        onClick={() => handleViewFeeds(kb)}
                        title="Manage Plugin Feeds"
                        color="secondary"
                      >
                        <FeedsIcon />
                      </IconButton>

                      <IconButton
                        size="small"
                        onClick={() => handleConfigureKB(kb)}
                        title="Configure RAG Settings"
                        color="info"
                      >
                        <ConfigIcon />
                      </IconButton>
                      <ExportKBButton kbId={kb.id} kbName={kb.name} />
                      {(kb.embedding_status === 'stale' || kb.embedding_status === 'error') && (
                        <IconButton
                          size="small"
                          onClick={() => setReEmbedTarget(kb)}
                          title="Re-embed Knowledge Base"
                          color="warning"
                        >
                          <RefreshIcon />
                        </IconButton>
                      )}
                      <IconButton size="small" onClick={() => handleDelete(kb.id)} title="Delete" color="error">
                        <DeleteIcon />
                      </IconButton>
                    </Box>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* Create Dialog */}
      <Dialog open={isCreateDialogOpen} onClose={() => setIsCreateDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Create Knowledge Base</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Name"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            sx={{ mb: 2, mt: 2 }}
          />
          <TextField
            fullWidth
            label="Description"
            multiline
            rows={3}
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            sx={{ mb: 2 }}
            helperText="After creating the knowledge base, use Plugin Feeds to configure data ingestion"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setIsCreateDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleCreate} variant="contained" disabled={createMutation.isLoading || !formData.name}>
            {createMutation.isLoading ? 'Creating...' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Edit Dialog */}
      <Dialog open={isEditDialogOpen} onClose={() => setIsEditDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Edit Knowledge Base</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Name"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            sx={{ mb: 2, mt: 2 }}
          />
          <TextField
            fullWidth
            label="Description"
            multiline
            rows={3}
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            sx={{ mb: 2 }}
            helperText="Use Plugin Feeds to configure data ingestion for this knowledge base"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setIsEditDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleUpdate} variant="contained" disabled={updateMutation.isLoading || !formData.name}>
            {updateMutation.isLoading ? 'Updating...' : 'Update'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* View Dialog */}
      <Dialog open={isViewDialogOpen} onClose={() => setIsViewDialogOpen(false)} maxWidth="lg" fullWidth>
        <DialogTitle>
          <Box display="flex" justifyContent="space-between" alignItems="center">
            <Typography variant="h6">{selectedKB?.name} - Details</Typography>
          </Box>
        </DialogTitle>
        <DialogContent>
          {selectedKB && (
            <Box>
              {/* Basic Info */}
              <Box mb={3}>
                <Typography variant="h6" gutterBottom>
                  Basic Information
                </Typography>
                <Typography variant="body1" paragraph>
                  <strong>Name:</strong> {selectedKB.name}
                </Typography>
                <Typography variant="body1" paragraph>
                  <strong>Description:</strong> {selectedKB.description || 'No description'}
                </Typography>
                <Typography variant="body1" paragraph>
                  <strong>Sync Enabled:</strong> {selectedKB.sync_enabled ? 'Yes' : 'No'}
                </Typography>
                <Typography variant="body1" paragraph>
                  <strong>Created:</strong> {new Date(selectedKB.created_at).toLocaleString()}
                </Typography>
              </Box>

              {/* Note: KB prompts are now managed at the model configuration level */}

              {/* Raw JSON (collapsible) */}
              <Box>
                <Typography variant="h6" gutterBottom>
                  Raw Data
                </Typography>
                <JSONPretty data={selectedKB} theme="monokai" />
              </Box>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setIsViewDialogOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>

      {/* KB Configuration Dialog */}
      <KBConfigDialog
        open={isConfigDialogOpen}
        onClose={() => {
          setIsConfigDialogOpen(false);
          setSelectedKBForManagement(null);
        }}
        knowledgeBase={selectedKBForManagement}
      />

      {/* Re-embed Confirmation Dialog */}
      <ReEmbedConfirmDialog
        knowledgeBase={reEmbedTarget}
        onClose={() => setReEmbedTarget(null)}
        onConfirm={() => reEmbedMutation.mutate(reEmbedTarget.id)}
        isLoading={reEmbedMutation.isLoading}
        error={reEmbedMutation.isError ? reEmbedMutation.error : null}
      />

      <ImportKBWizard
        open={isImportWizardOpen}
        onClose={() => setIsImportWizardOpen(false)}
        onSuccess={() => {
          queryClient.invalidateQueries('knowledgeBases');
        }}
      />
    </Box>
  );
}

export default KnowledgeBases;
