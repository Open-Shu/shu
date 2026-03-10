import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TablePagination,
  Paper,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Chip,
  Alert,
  CircularProgress,
} from '@mui/material';
import { Add as AddIcon, Edit as EditIcon, Delete as DeleteIcon, Policy as PolicyIcon } from '@mui/icons-material';
import { policyAPI, extractItemsFromResponse, extractPaginationFromResponse, formatError } from '../services/api';
import PageHelpHeader from './PageHelpHeader';

const POLICY_TEMPLATE = {
  name: 'example-policy',
  description: 'Allow Engineering group to read all experiences',
  effect: 'allow',
  is_active: true,
  bindings: [{ actor_type: 'group', actor_id: '<group-id>' }],
  statements: [{ actions: ['experience.read'], resources: ['experience:*'] }],
};

const PolicyAdmin = () => {
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [editorOpen, setEditorOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [selectedPolicy, setSelectedPolicy] = useState(null);
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState(null);
  const [error, setError] = useState(null);

  const queryClient = useQueryClient();

  const { data: policiesResponse, isLoading } = useQuery(
    ['policies', page, rowsPerPage],
    () => policyAPI.list({ offset: page * rowsPerPage, limit: rowsPerPage }),
    {
      keepPreviousData: true,
      onError: (err) => {
        setError(formatError(err));
      },
    }
  );

  const policies = extractItemsFromResponse(policiesResponse) || [];
  const pagination = policiesResponse ? extractPaginationFromResponse(policiesResponse) : null;
  const totalCount = pagination?.total || 0;

  const createMutation = useMutation((data) => policyAPI.create(data), {
    onSuccess: () => {
      queryClient.invalidateQueries(['policies']);
      setEditorOpen(false);
      setJsonError(null);
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err));
    },
  });

  const updateMutation = useMutation(({ id, data }) => policyAPI.update(id, data), {
    onSuccess: () => {
      queryClient.invalidateQueries(['policies']);
      setEditorOpen(false);
      setSelectedPolicy(null);
      setJsonError(null);
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err));
    },
  });

  const deleteMutation = useMutation((id) => policyAPI.delete(id), {
    onSuccess: () => {
      queryClient.invalidateQueries(['policies']);
      setDeleteDialogOpen(false);
      setSelectedPolicy(null);
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err));
    },
  });

  const handleCreate = () => {
    setSelectedPolicy(null);
    setJsonText(JSON.stringify(POLICY_TEMPLATE, null, 2));
    setJsonError(null);
    setEditorOpen(true);
  };

  const handleEdit = (policy) => {
    setSelectedPolicy(policy);
    const doc = {
      name: policy.name,
      description: policy.description,
      effect: policy.effect,
      is_active: policy.is_active,
      bindings: policy.bindings || [],
      statements: policy.statements || [],
    };
    setJsonText(JSON.stringify(doc, null, 2));
    setJsonError(null);
    setEditorOpen(true);
  };

  const handleDelete = (policy) => {
    setSelectedPolicy(policy);
    setDeleteDialogOpen(true);
  };

  const handleConfirmDelete = () => {
    if (selectedPolicy) {
      deleteMutation.mutate(selectedPolicy.id);
    }
  };

  const handleSave = () => {
    let parsed;
    try {
      parsed = JSON.parse(jsonText);
    } catch {
      setJsonError('Invalid JSON. Please check your syntax and try again.');
      return;
    }
    setJsonError(null);

    if (selectedPolicy) {
      updateMutation.mutate({ id: selectedPolicy.id, data: parsed });
    } else {
      createMutation.mutate(parsed);
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) {
      return 'N/A';
    }
    return new Date(dateString).toLocaleDateString();
  };

  const isSaving = createMutation.isLoading || updateMutation.isLoading;

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <PageHelpHeader
        title="Access Policies"
        description="Manage policy-based access control (PBAC) policies. Policies define which users and groups can perform actions on resources using allow/deny semantics."
        icon={<PolicyIcon />}
        tips={[
          'Policies use deny-wins semantics — a deny policy always overrides an allow policy',
          'Bind policies to users or groups via the bindings array',
          'Use wildcards in resources (e.g., "experience:*") to match all resources of a type',
          'Non-admin users with no matching allow policies are denied by default',
        ]}
        actions={
          <Button variant="contained" startIcon={<AddIcon />} onClick={handleCreate}>
            Create Policy
          </Button>
        }
      />

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <Card>
        <CardContent>
          <TableContainer component={Paper} variant="outlined">
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Effect</TableCell>
                  <TableCell>Active</TableCell>
                  <TableCell>Bindings</TableCell>
                  <TableCell>Statements</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {policies.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} align="center">
                      <Typography variant="body2" color="text.secondary">
                        No access policies found. Create your first policy to get started.
                      </Typography>
                    </TableCell>
                  </TableRow>
                ) : (
                  policies.map((policy) => (
                    <TableRow key={policy.id} hover>
                      <TableCell>
                        <Typography variant="body2" fontWeight="medium">
                          {policy.name}
                        </Typography>
                        {policy.description && (
                          <Typography variant="caption" color="text.secondary">
                            {policy.description}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={policy.effect}
                          color={policy.effect === 'allow' ? 'success' : 'error'}
                          size="small"
                        />
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={policy.is_active ? 'Active' : 'Inactive'}
                          color={policy.is_active ? 'success' : 'default'}
                          size="small"
                          variant="outlined"
                        />
                      </TableCell>
                      <TableCell>
                        <Chip label={`${(policy.bindings || []).length}`} size="small" variant="outlined" />
                      </TableCell>
                      <TableCell>
                        <Chip label={`${(policy.statements || []).length}`} size="small" variant="outlined" />
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" color="text.secondary">
                          {formatDate(policy.created_at)}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <IconButton size="small" aria-label="Edit policy" onClick={() => handleEdit(policy)}>
                          <EditIcon fontSize="small" />
                        </IconButton>
                        <IconButton
                          size="small"
                          color="error"
                          aria-label="Delete policy"
                          onClick={() => handleDelete(policy)}
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
            <TablePagination
              rowsPerPageOptions={[10, 25, 50]}
              component="div"
              count={totalCount}
              rowsPerPage={rowsPerPage}
              page={page}
              onPageChange={(_event, newPage) => setPage(newPage)}
              onRowsPerPageChange={(event) => {
                setRowsPerPage(parseInt(event.target.value, 10));
                setPage(0);
              }}
            />
          </TableContainer>
        </CardContent>
      </Card>

      {/* Create/Edit JSON Editor Dialog */}
      <Dialog open={editorOpen} onClose={() => setEditorOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{selectedPolicy ? 'Edit Policy' : 'Create Policy'}</DialogTitle>
        <DialogContent>
          {jsonError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {jsonError}
            </Alert>
          )}
          <TextField
            autoFocus
            fullWidth
            multiline
            rows={20}
            variant="outlined"
            value={jsonText}
            onChange={(e) => {
              setJsonText(e.target.value);
              setJsonError(null);
            }}
            InputProps={{
              sx: { fontFamily: 'monospace', fontSize: '0.875rem' },
            }}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditorOpen(false)}>Cancel</Button>
          <Button onClick={handleSave} variant="contained" disabled={isSaving}>
            {isSaving ? <CircularProgress size={20} /> : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)} maxWidth="sm">
        <DialogTitle>Delete Policy</DialogTitle>
        <DialogContent>
          <Typography>Are you sure you want to delete the policy &quot;{selectedPolicy?.name}&quot;?</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            This action cannot be undone. All associated bindings and statements will be removed.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleConfirmDelete} variant="contained" color="error" disabled={deleteMutation.isLoading}>
            {deleteMutation.isLoading ? <CircularProgress size={20} /> : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default PolicyAdmin;
