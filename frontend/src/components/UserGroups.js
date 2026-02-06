import { log } from '../utils/log';

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
  Paper,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Switch,
  FormControlLabel,
  Chip,
  Alert,
  CircularProgress,
  Menu,
  MenuItem,
  ListItemIcon,
  Autocomplete,
} from '@mui/material';
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  People as PeopleIcon,
  MoreVert as MoreVertIcon,
  Groups as GroupsIcon,
  Person as PersonIcon,
} from '@mui/icons-material';
import { groupsAPI, authAPI, extractItemsFromResponse, formatError } from '../services/api';
import AdminLayout from '../layouts/AdminLayout';
import PageHelpHeader from './PageHelpHeader';

const UserGroups = () => {
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [membersDialogOpen, setMembersDialogOpen] = useState(false);
  const [selectedGroup, setSelectedGroup] = useState(null);
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [menuGroup, setMenuGroup] = useState(null);
  const [error, setError] = useState(null);
  const [newGroup, setNewGroup] = useState({
    name: '',
    description: '',
    is_active: true,
  });
  const [newMember, setNewMember] = useState({
    user_id: '',
    role: 'member',
  });

  const queryClient = useQueryClient();

  // Fetch groups
  const { data: groupsResponse, isLoading } = useQuery('userGroups', groupsAPI.list, {
    onError: (err) => {
      setError(formatError(err));
    },
  });

  const groups = extractItemsFromResponse(groupsResponse) || [];

  // Fetch users for member management
  const { data: usersResponse } = useQuery('users', authAPI.getUsers, {
    onError: (err) => {
      log.error('Error fetching users:', err);
    },
  });

  const users = extractItemsFromResponse(usersResponse) || [];

  // Fetch group members when members dialog is open
  const { data: membersResponse, isLoading: membersLoading } = useQuery(
    ['groupMembers', selectedGroup?.id],
    () => groupsAPI.getMembers(selectedGroup.id),
    {
      enabled: !!selectedGroup?.id && membersDialogOpen,
      onError: (err) => {
        setError(formatError(err));
      },
    }
  );

  const members = extractItemsFromResponse(membersResponse) || [];

  // Create group mutation
  const createGroupMutation = useMutation((groupData) => groupsAPI.create(groupData), {
    onSuccess: () => {
      queryClient.invalidateQueries('userGroups');
      setCreateDialogOpen(false);
      setNewGroup({ name: '', description: '', is_active: true });
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err));
    },
  });

  // Update group mutation
  const updateGroupMutation = useMutation(({ groupId, data }) => groupsAPI.update(groupId, data), {
    onSuccess: () => {
      queryClient.invalidateQueries('userGroups');
      setEditDialogOpen(false);
      setSelectedGroup(null);
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err));
    },
  });

  // Delete group mutation
  const deleteGroupMutation = useMutation((groupId) => groupsAPI.delete(groupId), {
    onSuccess: () => {
      queryClient.invalidateQueries('userGroups');
      setDeleteDialogOpen(false);
      setSelectedGroup(null);
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err));
    },
  });

  // Add member mutation
  const addMemberMutation = useMutation(({ groupId, userId }) => groupsAPI.addMember(groupId, userId), {
    onSuccess: () => {
      queryClient.invalidateQueries(['groupMembers', selectedGroup?.id]);
      queryClient.invalidateQueries('userGroups');
      setNewMember({ user_id: '', role: 'member' });
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err));
    },
  });

  // Remove member mutation
  const removeMemberMutation = useMutation(({ groupId, userId }) => groupsAPI.removeMember(groupId, userId), {
    onSuccess: () => {
      queryClient.invalidateQueries(['groupMembers', selectedGroup?.id]);
      queryClient.invalidateQueries('userGroups');
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err));
    },
  });

  const handleCreateGroup = () => {
    if (newGroup.name.trim()) {
      createGroupMutation.mutate(newGroup);
    }
  };

  const handleEditGroup = (group) => {
    setSelectedGroup({
      ...group,
      is_active: group.is_active !== undefined ? group.is_active : true,
    });
    setEditDialogOpen(true);
    handleMenuClose();
  };

  const handleUpdateGroup = () => {
    if (selectedGroup && selectedGroup.name.trim()) {
      updateGroupMutation.mutate({
        groupId: selectedGroup.id,
        data: {
          name: selectedGroup.name,
          description: selectedGroup.description,
          is_active: selectedGroup.is_active,
        },
      });
    }
  };

  const handleDeleteGroup = (group) => {
    setSelectedGroup(group);
    setDeleteDialogOpen(true);
    handleMenuClose();
  };

  const handleConfirmDelete = () => {
    if (selectedGroup) {
      deleteGroupMutation.mutate(selectedGroup.id);
    }
  };

  const handleMenuOpen = (event, group) => {
    setMenuAnchor(event.currentTarget);
    setMenuGroup(group);
  };

  const handleMenuClose = () => {
    setMenuAnchor(null);
    setMenuGroup(null);
  };

  const handleManageMembers = (group) => {
    setSelectedGroup(group);
    setMembersDialogOpen(true);
    handleMenuClose();
  };

  const handleAddMember = () => {
    if (newMember.user_id && selectedGroup) {
      addMemberMutation.mutate({
        groupId: selectedGroup.id,
        userId: newMember.user_id,
      });
    }
  };

  const handleRemoveMember = (userId) => {
    if (selectedGroup) {
      removeMemberMutation.mutate({
        groupId: selectedGroup.id,
        userId: userId,
      });
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) {
      return 'N/A';
    }
    return new Date(dateString).toLocaleDateString();
  };

  if (isLoading) {
    return (
      <AdminLayout>
        <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
          <CircularProgress />
        </Box>
      </AdminLayout>
    );
  }

  return (
    <Box>
      <PageHelpHeader
        title="User Groups"
        description="Organize users into groups for easier permission management. Groups can be granted access to Knowledge Bases, making it simple to manage access for teams or departments."
        icon={<GroupsIcon />}
        tips={[
          'Create groups for teams, departments, or projects that need shared KB access',
          'Add users to groups via the Members button in the actions menu',
          'Groups can be used in KB Permissions to grant access to multiple users at once',
          'Deactivate groups to temporarily revoke their permissions without deleting',
        ]}
        actions={
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreateDialogOpen(true)}>
            Create Group
          </Button>
        }
      />

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
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
                  <TableCell>Description</TableCell>
                  <TableCell>Members</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {groups.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} align="center">
                      <Typography variant="body2" color="text.secondary">
                        No user groups found. Create your first group to get started.
                      </Typography>
                    </TableCell>
                  </TableRow>
                ) : (
                  groups.map((group) => (
                    <TableRow key={group.id} hover>
                      <TableCell>
                        <Box display="flex" alignItems="center">
                          <PeopleIcon sx={{ mr: 1, color: 'primary.main' }} />
                          <Typography variant="body2" fontWeight="medium">
                            {group.name}
                          </Typography>
                        </Box>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" color="text.secondary">
                          {group.description || 'No description'}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Chip label={`${group.member_count || 0} members`} size="small" variant="outlined" />
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={group.is_active ? 'Active' : 'Inactive'}
                          color={group.is_active ? 'success' : 'default'}
                          size="small"
                        />
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" color="text.secondary">
                          {formatDate(group.created_at)}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <IconButton onClick={(e) => handleMenuOpen(e, group)} size="small">
                          <MoreVertIcon />
                        </IconButton>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {/* Action Menu */}
      <Menu anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={handleMenuClose}>
        <MenuItem onClick={() => handleManageMembers(menuGroup)}>
          <ListItemIcon>
            <PeopleIcon fontSize="small" />
          </ListItemIcon>
          Manage Members
        </MenuItem>
        <MenuItem onClick={() => handleEditGroup(menuGroup)}>
          <ListItemIcon>
            <EditIcon fontSize="small" />
          </ListItemIcon>
          Edit Group
        </MenuItem>
        <MenuItem onClick={() => handleDeleteGroup(menuGroup)}>
          <ListItemIcon>
            <DeleteIcon fontSize="small" />
          </ListItemIcon>
          Delete Group
        </MenuItem>
      </Menu>

      {/* Create Group Dialog */}
      <Dialog open={createDialogOpen} onClose={() => setCreateDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Create New User Group</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="Group Name"
            fullWidth
            variant="outlined"
            value={newGroup.name}
            onChange={(e) => setNewGroup({ ...newGroup, name: e.target.value })}
            sx={{ mb: 2 }}
          />
          <TextField
            margin="dense"
            label="Description"
            fullWidth
            multiline
            rows={3}
            variant="outlined"
            value={newGroup.description}
            onChange={(e) => setNewGroup({ ...newGroup, description: e.target.value })}
            sx={{ mb: 2 }}
          />
          <FormControlLabel
            control={
              <Switch
                checked={newGroup.is_active}
                onChange={(e) => setNewGroup({ ...newGroup, is_active: e.target.checked })}
              />
            }
            label="Active"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleCreateGroup}
            variant="contained"
            disabled={!newGroup.name.trim() || createGroupMutation.isLoading}
          >
            {createGroupMutation.isLoading ? <CircularProgress size={20} /> : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Edit Group Dialog */}
      <Dialog open={editDialogOpen} onClose={() => setEditDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Edit User Group</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="Group Name"
            fullWidth
            variant="outlined"
            value={selectedGroup?.name || ''}
            onChange={(e) => setSelectedGroup({ ...selectedGroup, name: e.target.value })}
            sx={{ mb: 2 }}
          />
          <TextField
            margin="dense"
            label="Description"
            fullWidth
            multiline
            rows={3}
            variant="outlined"
            value={selectedGroup?.description || ''}
            onChange={(e) =>
              setSelectedGroup({
                ...selectedGroup,
                description: e.target.value,
              })
            }
            sx={{ mb: 2 }}
          />
          <FormControlLabel
            control={
              <Switch
                checked={selectedGroup?.is_active || false}
                onChange={(e) =>
                  setSelectedGroup({
                    ...selectedGroup,
                    is_active: e.target.checked,
                  })
                }
              />
            }
            label="Active"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleUpdateGroup}
            variant="contained"
            disabled={!selectedGroup?.name?.trim() || updateGroupMutation.isLoading}
          >
            {updateGroupMutation.isLoading ? <CircularProgress size={20} /> : 'Update'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Group Dialog */}
      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)} maxWidth="sm">
        <DialogTitle>Delete User Group</DialogTitle>
        <DialogContent>
          <Typography>Are you sure you want to delete the group "{selectedGroup?.name}"?</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            This action cannot be undone. All group memberships will be removed.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleConfirmDelete}
            variant="contained"
            color="error"
            disabled={deleteGroupMutation.isLoading}
          >
            {deleteGroupMutation.isLoading ? <CircularProgress size={20} /> : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Manage Members Dialog */}
      <Dialog open={membersDialogOpen} onClose={() => setMembersDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Manage Members - {selectedGroup?.name}</DialogTitle>
        <DialogContent>
          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          {/* Add Member Section */}
          <Box sx={{ mb: 3 }}>
            <Typography variant="h6" gutterBottom>
              Add Member
            </Typography>
            <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-end' }}>
              <Autocomplete
                options={users
                  // Ensure that only options that aren't already added are shown
                  .filter((user) => !members.some((member) => member.user_id === user.user_id))}
                getOptionLabel={(option) => `${option.email} (${option.name || 'No name'})`}
                value={users.find((user) => user.user_id === newMember.user_id) || null}
                onChange={(event, newValue) => {
                  setNewMember((prev) => ({
                    ...prev,
                    user_id: newValue?.user_id || '',
                  }));
                }}
                renderInput={(params) => <TextField {...params} label="Select User" fullWidth />}
                sx={{ flexGrow: 1 }}
              />
              <Button
                onClick={handleAddMember}
                variant="contained"
                disabled={!newMember.user_id || addMemberMutation.isLoading}
                startIcon={<AddIcon />}
              >
                {addMemberMutation.isLoading ? <CircularProgress size={20} /> : 'Add'}
              </Button>
            </Box>
          </Box>

          {/* Current Members Section */}
          <Box>
            <Typography variant="h6" gutterBottom>
              Current Members ({members.length})
            </Typography>
            {membersLoading ? (
              <Box display="flex" justifyContent="center" p={3}>
                <CircularProgress />
              </Box>
            ) : members.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No members in this group.
              </Typography>
            ) : (
              <TableContainer component={Paper} variant="outlined">
                <Table>
                  <TableHead>
                    <TableRow>
                      <TableCell>User</TableCell>
                      <TableCell>Role</TableCell>
                      <TableCell>Added</TableCell>
                      <TableCell align="right">Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {members.map((member) => (
                      <TableRow key={member.id} hover>
                        <TableCell>
                          <Box display="flex" alignItems="center">
                            <PersonIcon sx={{ mr: 1, color: 'primary.main' }} />
                            <Box>
                              <Typography variant="body2" fontWeight="medium">
                                {member.user_email || 'Unknown User'}
                              </Typography>
                              <Typography variant="caption" color="text.secondary">
                                {member.user_name || 'No name'}
                              </Typography>
                            </Box>
                          </Box>
                        </TableCell>
                        <TableCell>
                          <Chip label={member.role} size="small" color="primary" />
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {formatDate(member.granted_at)}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <IconButton
                            onClick={() => handleRemoveMember(member.user_id)}
                            size="small"
                            color="error"
                            disabled={removeMemberMutation.isLoading}
                          >
                            <DeleteIcon />
                          </IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMembersDialogOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default UserGroups;
