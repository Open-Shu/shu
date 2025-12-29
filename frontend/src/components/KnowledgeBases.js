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
  CircularProgress,


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
} from '@mui/icons-material';
import PageHelpHeader from './PageHelpHeader';

import { knowledgeBaseAPI, formatError, extractItemsFromResponse } from '../services/api';
import { useNavigate } from 'react-router-dom';



import KBConfigDialog from './KBConfigDialog';
import JSONPretty from 'react-json-pretty';
import 'react-json-pretty/themes/monikai.css';

function KnowledgeBases() {
  const navigate = useNavigate();
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

  const { data: knowledgeBasesResponse, isLoading, error, refetch } = useQuery(
    'knowledgeBases',
    knowledgeBaseAPI.list
  );

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

  const updateMutation = useMutation(
    ({ id, data }) => knowledgeBaseAPI.update(id, data),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('knowledgeBases');
        setIsEditDialogOpen(false);
      },
    }
  );

  const deleteMutation = useMutation(knowledgeBaseAPI.delete, {
    onSuccess: () => {
      queryClient.invalidateQueries('knowledgeBases');
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
    return (
      <Alert severity="error">
        Error loading knowledge bases: {formatError(error).message}
      </Alert>
    );
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
      <Box display="flex" justifyContent="flex-end" alignItems="center" mb={2}>
        <Box>
          <Button
            variant="outlined"
            onClick={() => refetch()}
            sx={{ mr: 2 }}
          >
            Refresh
          </Button>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setIsCreateDialogOpen(true)}
          >
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
                    <Chip
                      label={kb.status}
                      color={kb.status === 'active' ? 'success' : 'default'}
                      size="small"
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">
                      {kb.document_count || 0}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">
                      {kb.total_chunks || 0}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={kb.sync_enabled ? 'Enabled' : 'Disabled'}
                      color={kb.sync_enabled ? 'success' : 'error'}
                      size="small"
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">
                      {new Date(kb.created_at).toLocaleDateString()}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">
                      {kb.last_sync_at
                        ? new Date(kb.last_sync_at).toLocaleDateString()
                        : 'Never'
                      }
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Box display="flex" gap={0.5}>
                      <IconButton
                        size="small"
                        onClick={() => handleView(kb)}
                        title="View Details"
                      >
                        <ViewIcon />
                      </IconButton>
                      <IconButton
                        size="small"
                        onClick={() => handleViewDocuments(kb)}
                        title="View Documents"
                        color="primary"
                      >
                        <DocumentsIcon />
                      </IconButton>
                      <IconButton
                        size="small"
                        onClick={() => handleEdit(kb)}
                        title="Edit"
                      >
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
                      <IconButton
                        size="small"
                        onClick={() => handleDelete(kb.id)}
                        title="Delete"
                        color="error"
                      >
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
          <Button
            onClick={handleCreate}
            variant="contained"
            disabled={createMutation.isLoading || !formData.name}
          >
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
          <Button
            onClick={handleUpdate}
            variant="contained"
            disabled={updateMutation.isLoading || !formData.name}
          >
            {updateMutation.isLoading ? 'Updating...' : 'Update'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* View Dialog */}
      <Dialog open={isViewDialogOpen} onClose={() => setIsViewDialogOpen(false)} maxWidth="lg" fullWidth>
        <DialogTitle>
          <Box display="flex" justifyContent="space-between" alignItems="center">
            <Typography variant="h6">
              {selectedKB?.name} - Details
            </Typography>

          </Box>
        </DialogTitle>
        <DialogContent>
          {selectedKB && (
            <Box>
              {/* Basic Info */}
              <Box mb={3}>
                <Typography variant="h6" gutterBottom>Basic Information</Typography>
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
                <Typography variant="h6" gutterBottom>Raw Data</Typography>
                <JSONPretty
                  data={selectedKB}
                  theme="monokai"
                />
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
    </Box>
  );
}

export default KnowledgeBases;