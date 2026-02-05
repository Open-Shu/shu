import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
  Box,
  Paper,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Switch,
  FormControlLabel,
  Alert,
  CircularProgress,
  TextField,
} from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import BlockIcon from '@mui/icons-material/Block';
import PeopleIcon from '@mui/icons-material/People';
import { authAPI, extractDataFromResponse, formatError } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import PageHelpHeader from './PageHelpHeader';

const resolveUserId = (user) => {
  if (!user) {
    return '';
  }
  return user.user_id || user.id || '';
};

const UserManagement = () => {
  const { canManageUsers } = useAuth();
  const [editUser, setEditUser] = useState(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [userToDelete, setUserToDelete] = useState(null);
  const [newUser, setNewUser] = useState({
    email: '',
    name: '',
    password: '',
    role: 'regular_user',
    auth_method: 'password',
  });
  const [error, setError] = useState(null);
  const queryClient = useQueryClient();

  // Fetch users
  const { data: usersResponse, isLoading } = useQuery('users', authAPI.getUsers, {
    enabled: canManageUsers(),
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  // Update user mutation
  const updateUserMutation = useMutation(({ userId, data }) => authAPI.updateUser(userId, data), {
    onSuccess: () => {
      queryClient.invalidateQueries('users');
      setEditDialogOpen(false);
      setEditUser(null);
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  // Create user mutation
  const createUserMutation = useMutation((userData) => authAPI.createUser(userData), {
    onSuccess: () => {
      queryClient.invalidateQueries('users');
      setCreateDialogOpen(false);
      setNewUser({
        email: '',
        name: '',
        password: '',
        role: 'regular_user',
        auth_method: 'password',
      });
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  // Delete user mutation
  const deleteUserMutation = useMutation((userId) => authAPI.deleteUser(userId), {
    onSuccess: () => {
      queryClient.invalidateQueries('users');
      setDeleteDialogOpen(false);
      setUserToDelete(null);
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  // Activate user mutation
  const activateUserMutation = useMutation((userId) => authAPI.activateUser(userId), {
    onSuccess: () => {
      queryClient.invalidateQueries('users');
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  // Deactivate user mutation
  const deactivateUserMutation = useMutation((userId) => authAPI.deactivateUser(userId), {
    onSuccess: () => {
      queryClient.invalidateQueries('users');
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  if (!canManageUsers()) {
    return <Alert severity="error">You don't have permission to manage users.</Alert>;
  }

  const users = extractDataFromResponse(usersResponse) || [];

  const handleEditUser = (user) => {
    const userId = resolveUserId(user);
    if (!userId) {
      setError('Unable to edit user: missing user identifier');
      return;
    }
    setEditUser({
      ...user,
      id: userId,
      // Ensure all form fields have defined values
      role: user.role || 'regular_user',
      is_active: user.is_active !== undefined ? user.is_active : true,
    });
    setEditDialogOpen(true);
  };

  const handleSaveUser = () => {
    if (editUser) {
      const userId = resolveUserId(editUser);
      if (!userId) {
        setError('Unable to update user: missing user identifier');
        return;
      }
      updateUserMutation.mutate({
        userId,
        data: {
          role: editUser.role,
          is_active: editUser.is_active,
        },
      });
    }
  };

  const handleCreateUser = () => {
    if (newUser.email && newUser.name && newUser.password) {
      createUserMutation.mutate(newUser);
    }
  };

  const handleDeleteUser = (user) => {
    const userId = resolveUserId(user);
    if (!userId) {
      setError('Unable to delete user: missing user identifier');
      return;
    }
    setUserToDelete({ ...user, id: userId });
    setDeleteDialogOpen(true);
  };

  const handleConfirmDelete = () => {
    if (userToDelete) {
      const userId = resolveUserId(userToDelete);
      if (!userId) {
        setError('Unable to delete user: missing user identifier');
        return;
      }
      deleteUserMutation.mutate(userId);
    }
  };

  const handleOpenCreateDialog = () => {
    setNewUser({
      email: '',
      name: '',
      password: '',
      role: 'regular_user',
      auth_method: 'password',
    });
    setCreateDialogOpen(true);
    setError(null);
  };

  const getRoleColor = (role) => {
    const colors = {
      admin: 'error',
      power_user: 'warning',
      regular_user: 'primary',
    };
    return colors[role] || 'default';
  };

  const getRoleLabel = (role) => {
    const labels = {
      admin: 'Admin',
      power_user: 'Power User',
      regular_user: 'Regular User',
    };
    return labels[role] || role;
  };

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" p={3}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ position: 'relative' }}>
      <PageHelpHeader
        title="User Management"
        description="Manage user accounts, roles, and access. Create new users, assign roles (Admin, Power User, Regular User), and activate or deactivate accounts as needed."
        icon={<PeopleIcon />}
        tips={[
          'Admins have full access to all admin features including user management',
          'Power Users can manage prompts, models, and knowledge bases but not users',
          'Regular Users can only access the chat interface and their own settings',
          'Deactivated users cannot log in but their data is preserved',
          'Users can authenticate via password or Google SSO depending on auth method',
        ]}
        actions={
          <Button variant="contained" startIcon={<AddIcon />} onClick={handleOpenCreateDialog}>
            Create User
          </Button>
        }
      />

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <Paper>
        <TableContainer>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Email</TableCell>
                <TableCell>Role</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Last Login</TableCell>
                <TableCell>Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {users.map((user) => {
                const userId = resolveUserId(user);
                return (
                  <TableRow key={userId || user.email}>
                    <TableCell>{user.name}</TableCell>
                    <TableCell>{user.email}</TableCell>
                    <TableCell>
                      <Chip label={getRoleLabel(user.role)} color={getRoleColor(user.role)} size="small" />
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={user.is_active ? 'Active' : 'Inactive'}
                        color={user.is_active ? 'success' : 'default'}
                        size="small"
                      />
                    </TableCell>
                    <TableCell>{user.last_login ? new Date(user.last_login).toLocaleDateString() : 'Never'}</TableCell>
                    <TableCell>
                      {!user.is_active ? (
                        <IconButton
                          onClick={() => activateUserMutation.mutate(userId)}
                          size="small"
                          color="success"
                          title="Activate User"
                          disabled={activateUserMutation.isLoading || !userId}
                        >
                          <CheckCircleIcon />
                        </IconButton>
                      ) : (
                        <IconButton
                          onClick={() => deactivateUserMutation.mutate(userId)}
                          size="small"
                          color="warning"
                          title="Deactivate User"
                          disabled={deactivateUserMutation.isLoading || !userId}
                        >
                          <BlockIcon />
                        </IconButton>
                      )}
                      <IconButton
                        onClick={() => handleEditUser(user)}
                        size="small"
                        color="primary"
                        title="Edit User"
                        disabled={!userId}
                      >
                        <EditIcon />
                      </IconButton>
                      <IconButton
                        onClick={() => handleDeleteUser(user)}
                        size="small"
                        color="error"
                        title="Delete User"
                        disabled={deleteUserMutation.isLoading || !userId}
                      >
                        <DeleteIcon />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {/* Edit User Dialog */}
      <Dialog open={editDialogOpen} onClose={() => setEditDialogOpen(false)}>
        <DialogTitle>Edit User</DialogTitle>
        <DialogContent>
          {editUser && (
            <Box sx={{ pt: 1 }}>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                {editUser.name} ({editUser.email})
              </Typography>

              <FormControl fullWidth sx={{ mt: 2 }}>
                <InputLabel>Role</InputLabel>
                <Select
                  value={editUser?.role || ''}
                  label="Role"
                  onChange={(e) => setEditUser({ ...editUser, role: e.target.value })}
                >
                  <MenuItem value="regular_user">Regular User</MenuItem>
                  <MenuItem value="power_user">Power User</MenuItem>
                  <MenuItem value="admin">Admin</MenuItem>
                </Select>
              </FormControl>

              <FormControlLabel
                control={
                  <Switch
                    checked={editUser?.is_active || false}
                    onChange={(e) => setEditUser({ ...editUser, is_active: e.target.checked })}
                  />
                }
                label="Active"
                sx={{ mt: 2 }}
              />
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleSaveUser} variant="contained" disabled={updateUserMutation.isLoading}>
            {updateUserMutation.isLoading ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Create User Dialog */}
      <Dialog open={createDialogOpen} onClose={() => setCreateDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Create New User</DialogTitle>
        <DialogContent>
          <Box sx={{ pt: 1 }}>
            <TextField
              fullWidth
              label="Full Name"
              value={newUser.name}
              onChange={(e) => setNewUser({ ...newUser, name: e.target.value })}
              margin="normal"
              required
            />

            <TextField
              fullWidth
              label="Email Address"
              type="email"
              value={newUser.email}
              onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
              margin="normal"
              required
            />

            <TextField
              fullWidth
              label="Password"
              type="password"
              value={newUser.password}
              onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
              margin="normal"
              required
              helperText="Password must be at least 8 characters long"
            />

            <FormControl fullWidth sx={{ mt: 2 }}>
              <InputLabel>Role</InputLabel>
              <Select
                value={newUser.role}
                label="Role"
                onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
              >
                <MenuItem value="regular_user">Regular User</MenuItem>
                <MenuItem value="power_user">Power User</MenuItem>
                <MenuItem value="admin">Admin</MenuItem>
              </Select>
            </FormControl>

            <FormControl fullWidth sx={{ mt: 2 }}>
              <InputLabel>Authentication Method</InputLabel>
              <Select
                value={newUser.auth_method}
                label="Authentication Method"
                onChange={(e) => setNewUser({ ...newUser, auth_method: e.target.value })}
              >
                <MenuItem value="password">Password</MenuItem>
                <MenuItem value="google">Google OAuth</MenuItem>
              </Select>
            </FormControl>

            {newUser.auth_method === 'google' && (
              <Alert severity="info" sx={{ mt: 2 }}>
                Google OAuth users will need to sign in with their Google account. No password is required.
              </Alert>
            )}

            <Alert severity="warning" sx={{ mt: 2 }}>
              <strong>Security Note:</strong> Admin-created users are automatically activated. Self-registered users
              require manual activation for security.
            </Alert>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleCreateUser}
            variant="contained"
            disabled={
              createUserMutation.isLoading ||
              !newUser.email ||
              !newUser.name ||
              (newUser.auth_method === 'password' && !newUser.password)
            }
          >
            {createUserMutation.isLoading ? 'Creating...' : 'Create User'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete User Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Delete User</DialogTitle>
        <DialogContent>
          {userToDelete && (
            <Box sx={{ pt: 1 }}>
              <Typography variant="body1" gutterBottom>
                Are you sure you want to delete this user?
              </Typography>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                <strong>Name:</strong> {userToDelete.name}
              </Typography>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                <strong>Email:</strong> {userToDelete.email}
              </Typography>
              <Alert severity="warning" sx={{ mt: 2 }}>
                <strong>Warning:</strong> This action cannot be undone. All user data and access will be permanently
                removed.
              </Alert>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleConfirmDelete}
            variant="contained"
            color="error"
            disabled={deleteUserMutation.isLoading}
          >
            {deleteUserMutation.isLoading ? 'Deleting...' : 'Delete User'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default UserManagement;
