import React, { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { useSearchParams } from 'react-router-dom';
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
  Chip,
  Alert,
  CircularProgress,
} from '@mui/material';
import { Add as AddIcon, Edit as EditIcon, Delete as DeleteIcon, Policy as PolicyIcon } from '@mui/icons-material';
import {
  policyAPI,
  extractDataFromResponse,
  extractItemsFromResponse,
  extractPaginationFromResponse,
  formatError,
} from '../services/api';
import PageHelpHeader from './PageHelpHeader';
import PolicyEditorDialog from './PolicyEditorDialog';

const formatDate = (dateString) => {
  if (!dateString) {
    return 'N/A';
  }
  return new Date(dateString).toLocaleDateString();
};

const PolicyRow = ({ policy, onEdit, onDelete }) => (
  <TableRow hover>
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
      <Chip label={policy.effect} color={policy.effect === 'allow' ? 'success' : 'error'} size="small" />
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
      <IconButton size="small" aria-label="Edit policy" onClick={() => onEdit(policy)}>
        <EditIcon fontSize="small" />
      </IconButton>
      <IconButton size="small" color="error" aria-label="Delete policy" onClick={() => onDelete(policy)}>
        <DeleteIcon fontSize="small" />
      </IconButton>
    </TableCell>
  </TableRow>
);

const DeletePolicyDialog = ({ open, onClose, policy, onConfirm, isDeleting }) => (
  <Dialog open={open} onClose={onClose} maxWidth="sm">
    <DialogTitle>Delete Policy</DialogTitle>
    <DialogContent>
      <Typography>Are you sure you want to delete the policy &quot;{policy?.name}&quot;?</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        This action cannot be undone. All associated bindings and statements will be removed.
      </Typography>
    </DialogContent>
    <DialogActions>
      <Button onClick={onClose}>Cancel</Button>
      <Button onClick={onConfirm} variant="contained" color="error" disabled={isDeleting}>
        {isDeleting ? <CircularProgress size={20} /> : 'Delete'}
      </Button>
    </DialogActions>
  </Dialog>
);

const PolicyAdmin = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [editorOpen, setEditorOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [selectedPolicy, setSelectedPolicy] = useState(null);
  const [error, setError] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [pendingPolicyId, setPendingPolicyId] = useState(() => searchParams.get('policyId'));

  const queryClient = useQueryClient();

  const { data: policiesResponse, isLoading } = useQuery(
    ['policies', page, rowsPerPage],
    () => policyAPI.list({ offset: page * rowsPerPage, limit: rowsPerPage }),
    { keepPreviousData: true, onError: (err) => setError(formatError(err)) }
  );

  const policies = extractItemsFromResponse(policiesResponse) || [];
  const pagination = policiesResponse ? extractPaginationFromResponse(policiesResponse) : null;
  const totalCount = pagination?.total || 0;

  const createMutation = useMutation((data) => policyAPI.create(data), {
    onSuccess: () => {
      queryClient.invalidateQueries(['policies']);
      closeEditor();
    },
    onError: (err) => {
      setSaveError(formatError(err));
    },
  });

  const updateMutation = useMutation(({ id, data }) => policyAPI.update(id, data), {
    onSuccess: () => {
      queryClient.invalidateQueries(['policies']);
      closeEditor();
    },
    onError: (err) => {
      setSaveError(formatError(err));
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

  const clearPolicyParam = useCallback(() => {
    setSearchParams({}, { replace: true });
  }, [setSearchParams]);

  const closeEditor = useCallback(() => {
    setEditorOpen(false);
    setSelectedPolicy(null);
    setSaveError(null);
    clearPolicyParam();
  }, [clearPolicyParam]);

  const handleCreate = () => {
    setSelectedPolicy(null);
    setEditorOpen(true);
    clearPolicyParam();
  };

  const handleEdit = useCallback(
    (policy) => {
      setSelectedPolicy(policy);
      setEditorOpen(true);
      setSearchParams({ policyId: policy.id }, { replace: true });
    },
    [setSearchParams]
  );

  const handleDeepLinkError = useCallback(
    (err) => {
      setError(formatError(err));
      setPendingPolicyId(null);
      clearPolicyParam();
    },
    [clearPolicyParam]
  );

  const { data: deepLinkedPolicy } = useQuery(
    ['policy', pendingPolicyId],
    () => policyAPI.get(pendingPolicyId).then(extractDataFromResponse),
    { enabled: !!pendingPolicyId, onError: handleDeepLinkError }
  );

  useEffect(() => {
    if (!deepLinkedPolicy) {
      return;
    }
    handleEdit(deepLinkedPolicy);
    setPendingPolicyId(null);
  }, [deepLinkedPolicy, handleEdit]);

  const handleDelete = (policy) => {
    setSelectedPolicy(policy);
    setDeleteDialogOpen(true);
  };

  const handleConfirmDelete = () => selectedPolicy && deleteMutation.mutate(selectedPolicy.id);

  const handleSave = (data) => {
    if (selectedPolicy) {
      updateMutation.mutate({ id: selectedPolicy.id, data });
    } else {
      createMutation.mutate(data);
    }
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
                    <PolicyRow key={policy.id} policy={policy} onEdit={handleEdit} onDelete={handleDelete} />
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

      <PolicyEditorDialog
        open={editorOpen}
        onClose={closeEditor}
        policy={selectedPolicy}
        onSave={handleSave}
        isSaving={isSaving}
        saveError={saveError}
      />

      <DeletePolicyDialog
        open={deleteDialogOpen}
        onClose={() => setDeleteDialogOpen(false)}
        policy={selectedPolicy}
        onConfirm={handleConfirmDelete}
        isDeleting={deleteMutation.isLoading}
      />
    </Box>
  );
};

export default PolicyAdmin;
