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
  Tooltip,
} from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import BlockIcon from '@mui/icons-material/Block';
import LockResetIcon from '@mui/icons-material/LockReset';
import PeopleIcon from '@mui/icons-material/People';
import ShieldIcon from '@mui/icons-material/Shield';
import EventBusyIcon from '@mui/icons-material/EventBusy';
import UndoIcon from '@mui/icons-material/Undo';
import EventSeatIcon from '@mui/icons-material/EventSeat';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { authAPI, billingAPI, extractDataFromResponse, formatError } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { resolveUserId } from '../utils/userHelpers';
import PageHelpHeader from './PageHelpHeader';
import ResetPasswordDialog from './ResetPasswordDialog';
import EffectivePermissionsDialog from './EffectivePermissionsDialog';
import SeatLimitModal from './SeatLimitModal';

const UserManagement = () => {
  const { canManageUsers } = useAuth();
  const [editUser, setEditUser] = useState(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [userToDelete, setUserToDelete] = useState(null);
  const [resetPasswordUser, setResetPasswordUser] = useState(null);
  const [permissionsUser, setPermissionsUser] = useState(null);
  // SHU-507: when an admin clicks Activate on a password user whose email is
  // not yet verified, prompt for confirmation. Activating alone does not let
  // them log in — the email_verified gate is independent — and admins
  // otherwise have no signal that the second gate exists.
  const [unverifiedActivateUser, setUnverifiedActivateUser] = useState(null);
  const [newUser, setNewUser] = useState({
    email: '',
    name: '',
    password: '',
    role: 'regular_user',
    auth_method: 'password',
  });
  const [error, setError] = useState(null);
  const [seatLimitPrompt, setSeatLimitPrompt] = useState(null);
  const queryClient = useQueryClient();

  // Fetch users
  const { data: usersResponse, isLoading } = useQuery('users', authAPI.getUsers, {
    enabled: canManageUsers(),
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  const { data: subscriptionResponse } = useQuery('billing-subscription', billingAPI.getSubscription, {
    enabled: canManageUsers(),
  });
  const subscription = extractDataFromResponse(subscriptionResponse) || {};
  const isSeatGateActive = subscription.user_limit_enforcement === 'hard';

  // Treat a 402 seat_limit_reached response as a phase-1 preview instead
  // of an error: pop the consent modal, and remember the retry closure so
  // the Add 1 seat button can re-issue the original call with the header.
  const extractSeatLimitDetails = (err) => {
    const data = err?.response?.data?.error;
    if (err?.response?.status !== 402 || data?.code !== 'seat_limit_reached') {
      return null;
    }
    return data.details || {};
  };

  // Update user mutation
  const updateUserMutation = useMutation(
    ({ userId, data, confirmSeatCharge = false }) => authAPI.updateUser(userId, data, { confirmSeatCharge }),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('users');
        queryClient.invalidateQueries('billing-subscription');
        setEditDialogOpen(false);
        setEditUser(null);
        setSeatLimitPrompt(null);
        setError(null);
      },
      onError: (err, variables) => {
        // A False→True flip on is_active can trigger the seat-charge preflight.
        // Mirror createUserMutation's 402 handling so the modal can confirm.
        const seatDetails = extractSeatLimitDetails(err);
        if (seatDetails) {
          setSeatLimitPrompt({
            details: seatDetails,
            retry: () =>
              updateUserMutation.mutate({
                userId: variables.userId,
                data: variables.data,
                confirmSeatCharge: true,
              }),
          });
          return;
        }
        setError(formatError(err).message);
      },
    }
  );

  // Create user mutation
  const createUserMutation = useMutation(
    ({ userData, confirmSeatCharge = false }) => authAPI.createUser(userData, { confirmSeatCharge }),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('users');
        queryClient.invalidateQueries('billing-subscription');
        setCreateDialogOpen(false);
        setSeatLimitPrompt(null);
        setNewUser({
          email: '',
          name: '',
          password: '',
          role: 'regular_user',
          auth_method: 'password',
        });
        setError(null);
      },
      onError: (err, variables) => {
        const seatDetails = extractSeatLimitDetails(err);
        if (seatDetails) {
          setSeatLimitPrompt({
            details: seatDetails,
            retry: () =>
              createUserMutation.mutate({
                userData: variables.userData,
                confirmSeatCharge: true,
              }),
          });
          return;
        }
        setError(formatError(err).message);
      },
    }
  );

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
  const activateUserMutation = useMutation(
    ({ userId, confirmSeatCharge = false }) => authAPI.activateUser(userId, { confirmSeatCharge }),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('users');
        queryClient.invalidateQueries('billing-subscription');
        setSeatLimitPrompt(null);
        setError(null);
      },
      onError: (err, variables) => {
        const seatDetails = extractSeatLimitDetails(err);
        if (seatDetails) {
          setSeatLimitPrompt({
            details: seatDetails,
            retry: () =>
              activateUserMutation.mutate({
                userId: variables.userId,
                confirmSeatCharge: true,
              }),
          });
          return;
        }
        setError(formatError(err).message);
      },
    }
  );

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

  const scheduleDeactivationMutation = useMutation((userId) => authAPI.scheduleUserDeactivation(userId), {
    onSuccess: () => {
      queryClient.invalidateQueries('users');
      queryClient.invalidateQueries('billing-subscription');
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  const unscheduleDeactivationMutation = useMutation((userId) => authAPI.unscheduleUserDeactivation(userId), {
    onSuccess: () => {
      queryClient.invalidateQueries('users');
      queryClient.invalidateQueries('billing-subscription');
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  const releaseSeatMutation = useMutation(() => billingAPI.releaseSeat(), {
    onSuccess: () => {
      queryClient.invalidateQueries('billing-subscription');
      setError(null);
    },
    onError: (err) => {
      setError(formatError(err).message);
    },
  });

  const cancelPendingReleaseMutation = useMutation(() => billingAPI.cancelPendingRelease(), {
    onSuccess: () => {
      // Both billing-subscription (target_quantity) and users (cleared flags)
      // change in one shot; refresh both.
      queryClient.invalidateQueries('billing-subscription');
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
  const activeUserCount = users.filter((u) => u.is_active).length;
  const stripeQuantity = subscription.user_limit ?? 0;
  const targetQuantity = subscription.target_quantity ?? stripeQuantity;
  const openSeats = Math.max(0, stripeQuantity - activeUserCount);
  // Release is only allowed when there's true headroom — target_quantity
  // strictly greater than active count. Otherwise the admin should flag a
  // specific user, since a release without headroom would force a random
  // trim at rollover.
  const canRelease = targetQuantity > activeUserCount;
  // Pending change exists when admin has scheduled an up- or downgrade that
  // hasn't yet materialised on Stripe's live phase-1 quantity.
  const hasPendingChange = targetQuantity !== stripeQuantity;

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
      createUserMutation.mutate({ userData: newUser });
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
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            {isSeatGateActive && (
              <>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}
                >
                  <EventSeatIcon fontSize="small" />
                  {openSeats} open seat{openSeats === 1 ? '' : 's'}
                </Typography>
                {hasPendingChange && (
                  <Typography variant="body2" color="warning.main">
                    {stripeQuantity} now → {targetQuantity}
                    {subscription.current_period_end
                      ? ` after ${new Date(subscription.current_period_end).toLocaleDateString()}`
                      : ''}
                  </Typography>
                )}
                {targetQuantity < stripeQuantity && (
                  <Tooltip title="Releases the Stripe downgrade schedule and clears every user's scheduled deactivation. Affects all pending seat reductions, not just the most recent one.">
                    <Button
                      variant="outlined"
                      size="small"
                      color="warning"
                      onClick={() => {
                        const flagged = users.filter((u) => u.deactivation_scheduled_at).length;
                        const summary =
                          flagged > 0
                            ? `Cancel all pending seat reductions? This will release ${
                                stripeQuantity - targetQuantity
                              } seat(s) and unflag ${flagged} user(s) currently scheduled for deactivation.`
                            : `Cancel pending seat reduction(s)? Stripe will stay at ${stripeQuantity} seats next cycle.`;
                        if (window.confirm(summary)) {
                          cancelPendingReleaseMutation.mutate();
                        }
                      }}
                      disabled={cancelPendingReleaseMutation.isLoading}
                      aria-label="Cancel all pending seat reductions and unflag scheduled users"
                    >
                      Cancel all pending reductions
                    </Button>
                  </Tooltip>
                )}
                <Tooltip
                  title={
                    canRelease
                      ? ''
                      : 'No open seats. Schedule a user for deactivation to reduce capacity at the next billing cycle.'
                  }
                >
                  <span>
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={() => releaseSeatMutation.mutate()}
                      disabled={!canRelease || releaseSeatMutation.isLoading}
                      aria-label="Release one open seat"
                    >
                      Release 1 seat
                    </Button>
                  </span>
                </Tooltip>
              </>
            )}
            <Button variant="contained" startIcon={<AddIcon />} onClick={handleOpenCreateDialog}>
              Create User
            </Button>
          </Box>
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
                    <TableCell>
                      {user.name}
                      {isSeatGateActive && user.deactivation_scheduled_at && subscription.current_period_end && (
                        <Typography variant="caption" color="text.secondary" display="block">
                          Loses access on {new Date(subscription.current_period_end).toLocaleDateString()}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>{user.email}</TableCell>
                    <TableCell>
                      <Chip label={getRoleLabel(user.role)} color={getRoleColor(user.role)} size="small" />
                    </TableCell>
                    <TableCell>
                      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                        <Chip
                          label={user.is_active ? 'Active' : 'Inactive'}
                          color={user.is_active ? 'success' : 'default'}
                          size="small"
                        />
                        {user.auth_method === 'password' && user.email_verified === false && (
                          <Tooltip title="This user has not verified their email address. They cannot log in until they verify, even if Active.">
                            <Chip
                              icon={<WarningAmberIcon />}
                              label="Email unverified"
                              color="warning"
                              size="small"
                              variant="outlined"
                            />
                          </Tooltip>
                        )}
                      </Box>
                    </TableCell>
                    <TableCell>{user.last_login ? new Date(user.last_login).toLocaleDateString() : 'Never'}</TableCell>
                    <TableCell>
                      {!user.is_active ? (
                        <IconButton
                          onClick={() => {
                            // Surface the email_verified gate to the admin
                            // before they consume a seat on a user who still
                            // can't log in. See unverifiedActivateUser dialog
                            // below.
                            if (user.auth_method === 'password' && user.email_verified === false) {
                              setUnverifiedActivateUser({ ...user, id: userId });
                              return;
                            }
                            activateUserMutation.mutate({ userId });
                          }}
                          size="small"
                          color="success"
                          title="Activate User"
                          aria-label="Activate user"
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
                          aria-label="Deactivate user"
                          disabled={deactivateUserMutation.isLoading || !userId}
                        >
                          <BlockIcon />
                        </IconButton>
                      )}
                      {isSeatGateActive &&
                        user.is_active &&
                        (user.deactivation_scheduled_at ? (
                          <IconButton
                            onClick={() => unscheduleDeactivationMutation.mutate(userId)}
                            size="small"
                            color="info"
                            title="Cancel scheduled deactivation"
                            aria-label="Cancel scheduled deactivation"
                            disabled={unscheduleDeactivationMutation.isLoading || !userId}
                          >
                            <UndoIcon />
                          </IconButton>
                        ) : (
                          <IconButton
                            onClick={() => scheduleDeactivationMutation.mutate(userId)}
                            size="small"
                            color="warning"
                            title="Schedule deactivation on period end"
                            aria-label="Schedule deactivation on period end"
                            disabled={scheduleDeactivationMutation.isLoading || !userId}
                          >
                            <EventBusyIcon />
                          </IconButton>
                        ))}
                      <IconButton
                        onClick={() => handleEditUser(user)}
                        size="small"
                        color="primary"
                        title="Edit User"
                        disabled={!userId}
                      >
                        <EditIcon />
                      </IconButton>
                      {user.auth_method === 'password' && (
                        <IconButton
                          onClick={() => setResetPasswordUser(user)}
                          size="small"
                          color="warning"
                          title="Reset Password"
                          disabled={!userId}
                        >
                          <LockResetIcon />
                        </IconButton>
                      )}
                      <IconButton
                        onClick={() => setPermissionsUser(user)}
                        size="small"
                        color="info"
                        title="View Permissions"
                        disabled={!userId}
                      >
                        <ShieldIcon />
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

      {/* Effective Permissions Dialog */}
      <EffectivePermissionsDialog
        open={!!permissionsUser}
        onClose={() => setPermissionsUser(null)}
        user={permissionsUser}
      />

      {/* Reset Password Dialog */}
      <ResetPasswordDialog
        open={!!resetPasswordUser}
        onClose={() => setResetPasswordUser(null)}
        user={resetPasswordUser}
        onSuccess={() => queryClient.invalidateQueries('users')}
      />

      {/* Inline seat-charge consent modal (SHU-730 phase-2 confirmation) */}
      <SeatLimitModal
        open={!!seatLimitPrompt}
        details={seatLimitPrompt?.details}
        isConfirming={createUserMutation.isLoading || activateUserMutation.isLoading || updateUserMutation.isLoading}
        onClose={() => setSeatLimitPrompt(null)}
        onConfirm={() => seatLimitPrompt?.retry?.()}
      />

      {/* SHU-507: confirm activation of a password user whose email is not
          yet verified. Activation alone does not let them log in — the
          email_verified gate is independent and the admin needs to know that
          before consuming a seat on a user who still can't sign in. */}
      <Dialog open={!!unverifiedActivateUser} onClose={() => setUnverifiedActivateUser(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Activate user with unverified email?</DialogTitle>
        <DialogContent>
          <Alert severity="warning" sx={{ mb: 2 }}>
            <strong>{unverifiedActivateUser?.email}</strong> has not verified their email address.
          </Alert>
          <Typography variant="body2" sx={{ mb: 1 }}>
            Email verification is an independent gate from activation. Activating this user will consume a seat, but
            they still will not be able to log in until they click the verification link sent to their address.
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Consider waiting until they verify before activating, or have them request a new verification email from the
            sign-in screen if their original link expired.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUnverifiedActivateUser(null)}>Cancel</Button>
          <Button
            color="warning"
            variant="contained"
            onClick={() => {
              const userId = unverifiedActivateUser?.id;
              setUnverifiedActivateUser(null);
              if (userId) {
                activateUserMutation.mutate({ userId });
              }
            }}
            disabled={activateUserMutation.isLoading}
          >
            Activate anyway
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default UserManagement;
