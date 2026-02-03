import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "react-query";
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
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Chip,
  Alert,
  CircularProgress,
  Autocomplete,
  FormControlLabel,
  Switch,
  Grid,
} from "@mui/material";
import {
  Add as AddIcon,
  Delete as DeleteIcon,
  Security as SecurityIcon,
  Person as PersonIcon,
  Group as GroupIcon,
  Schedule as ScheduleIcon,
} from "@mui/icons-material";
import NotImplemented from "./NotImplemented";
// Removed date picker imports due to compatibility issues
import {
  knowledgeBaseAPI,
  groupsAPI,
  authAPI,
  extractItemsFromResponse,
  formatError,
} from "../services/api";
import AdminLayout from "../layouts/AdminLayout";
import { log } from "../utils/log";
import PageHelpHeader from "./PageHelpHeader";

const PERMISSION_LEVELS = [
  {
    value: "owner",
    label: "Owner",
    description: "Full control, can delete KB, manage permissions",
    color: "error",
  },
  {
    value: "admin",
    label: "Admin",
    description: "Can modify KB, add/remove documents, manage members",
    color: "warning",
  },
  {
    value: "member",
    label: "Member",
    description: "Can query KB, view documents, add documents",
    color: "primary",
  },
  {
    value: "read_only",
    label: "Read Only",
    description: "Can only query KB, no modifications",
    color: "default",
  },
];

const KBPermissions = () => {
  const [selectedKB, setSelectedKB] = useState("");
  const [grantDialogOpen, setGrantDialogOpen] = useState(false);
  const [bulkDialogOpen, setBulkDialogOpen] = useState(false);
  const [error, setError] = useState(null);
  const [newPermission, setNewPermission] = useState({
    target_type: "user", // 'user' or 'group'
    target_id: "",
    permission_level: "read_only",
    expires_at: null,
    has_expiration: false,
  });

  const queryClient = useQueryClient();

  // Fetch knowledge bases
  const { data: kbResponse, isLoading: kbLoading } = useQuery(
    "knowledgeBases",
    knowledgeBaseAPI.list,
    {
      onError: (err) => {
        setError(formatError(err).message);
      },
    },
  );

  const knowledgeBases = extractItemsFromResponse(kbResponse) || [];

  // Fetch users for autocomplete
  const { data: usersResponse } = useQuery("users", authAPI.getUsers, {
    enabled: newPermission.target_type === "user",
    onError: (err) => {
      log.error("Error fetching users:", err);
    },
  });

  const users = extractItemsFromResponse(usersResponse) || [];

  // Fetch groups for autocomplete
  const { data: groupsResponse } = useQuery("userGroups", groupsAPI.list, {
    enabled: newPermission.target_type === "group",
    onError: (err) => {
      log.error("Error fetching groups:", err);
    },
  });

  const groups = extractItemsFromResponse(groupsResponse) || [];

  // Fetch permissions for selected KB
  const { data: permissionsResponse, isLoading: permissionsLoading } = useQuery(
    ["kbPermissions", selectedKB],
    () => knowledgeBaseAPI.getPermissions(selectedKB),
    {
      enabled: !!selectedKB,
      onError: (err) => {
        setError(formatError(err).message);
      },
    },
  );

  const permissions = extractItemsFromResponse(permissionsResponse) || [];

  // Grant permission mutation
  const grantPermissionMutation = useMutation(
    (permissionData) =>
      knowledgeBaseAPI.grantPermission(selectedKB, permissionData),
    {
      onSuccess: () => {
        queryClient.invalidateQueries(["kbPermissions", selectedKB]);
        setGrantDialogOpen(false);
        setNewPermission({
          target_type: "user",
          target_id: "",
          permission_level: "read_only",
          expires_at: null,
          has_expiration: false,
        });
        setError(null);
      },
      onError: (err) => {
        setError(formatError(err).message);
      },
    },
  );

  // Revoke permission mutation
  const revokePermissionMutation = useMutation(
    (permissionId) =>
      knowledgeBaseAPI.revokePermission(selectedKB, permissionId),
    {
      onSuccess: () => {
        queryClient.invalidateQueries(["kbPermissions", selectedKB]);
        setError(null);
      },
      onError: (err) => {
        setError(formatError(err).message);
      },
    },
  );

  const handleGrantPermission = () => {
    if (!newPermission.target_id || !selectedKB) {
      return;
    }

    const permissionData = {
      permission_level: newPermission.permission_level,
      expires_at:
        newPermission.has_expiration && newPermission.expires_at
          ? new Date(newPermission.expires_at).toISOString()
          : null,
    };

    if (newPermission.target_type === "user") {
      permissionData.user_id = newPermission.target_id;
    } else {
      permissionData.group_id = newPermission.target_id;
    }

    log.debug(
      "Sending permission data:",
      permissionData,
      newPermission.target_type,
      newPermission.target_id,
    );

    grantPermissionMutation.mutate(permissionData);
  };

  const handleRevokePermission = (permissionId) => {
    if (window.confirm("Are you sure you want to revoke this permission?")) {
      revokePermissionMutation.mutate(permissionId);
    }
  };

  const getPermissionLevelInfo = (level) => {
    return (
      PERMISSION_LEVELS.find((p) => p.value === level) || PERMISSION_LEVELS[3]
    );
  };

  const formatDate = (dateString) => {
    if (!dateString) {
      return "Never";
    }
    return new Date(dateString).toLocaleDateString();
  };

  const getTargetOptions = () => {
    if (newPermission.target_type === "user") {
      return users.map((user) => ({
        id: user.user_id,
        label: `${user.name} (${user.email})`,
        value: user.user_id,
      }));
    } else {
      return groups.map((group) => ({
        id: group.id,
        label: group.name,
        value: group.id,
      }));
    }
  };

  if (kbLoading) {
    return (
      <AdminLayout>
        <Box
          display="flex"
          justifyContent="center"
          alignItems="center"
          minHeight="400px"
        >
          <CircularProgress />
        </Box>
      </AdminLayout>
    );
  }

  return (
    <Box>
      <PageHelpHeader
        title="Knowledge Base Permissions"
        description="Control who can access each Knowledge Base. Grant permissions to individual users or entire groups with different access levels: Owner, Admin, Member, or Read Only."
        icon={<SecurityIcon />}
        tips={[
          "Select a Knowledge Base first, then grant permissions to users or groups",
          "Owner: Full control including deletion and permission management",
          "Admin: Can modify KB and documents, but cannot delete the KB",
          "Member: Can query and add documents, but cannot delete",
          "Read Only: Can only search and view, no modifications allowed",
          "Use groups to grant the same permissions to multiple users at once",
        ]}
        actions={
          <Box display="flex" gap={2}>
            <Button
              variant="outlined"
              startIcon={<AddIcon />}
              onClick={() => setBulkDialogOpen(true)}
              disabled={!selectedKB}
            >
              Bulk Operations
            </Button>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={() => setGrantDialogOpen(true)}
              disabled={!selectedKB}
            >
              Grant Permission
            </Button>
          </Box>
        }
      />

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {/* Knowledge Base Selection */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <FormControl fullWidth>
            <InputLabel>Select Knowledge Base</InputLabel>
            <Select
              value={selectedKB}
              onChange={(e) => setSelectedKB(e.target.value)}
              label="Select Knowledge Base"
            >
              {knowledgeBases.map((kb) => (
                <MenuItem key={kb.id} value={kb.id}>
                  <Box display="flex" alignItems="center">
                    <SecurityIcon sx={{ mr: 1, color: "primary.main" }} />
                    {kb.name}
                  </Box>
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </CardContent>
      </Card>

      {/* Permissions Table */}
      {selectedKB && (
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Permissions for{" "}
              {knowledgeBases.find((kb) => kb.id === selectedKB)?.name}
            </Typography>

            {permissionsLoading ? (
              <Box display="flex" justifyContent="center" p={3}>
                <CircularProgress />
              </Box>
            ) : (
              <TableContainer component={Paper} variant="outlined">
                <Table>
                  <TableHead>
                    <TableRow>
                      <TableCell>Target</TableCell>
                      <TableCell>Permission Level</TableCell>
                      <TableCell>Granted By</TableCell>
                      <TableCell>Granted At</TableCell>
                      <TableCell>Expires</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell align="right">Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {permissions.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={7} align="center">
                          <Typography variant="body2" color="text.secondary">
                            No permissions found. Grant permissions to users or
                            groups to get started.
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ) : (
                      permissions.map((permission) => {
                        const levelInfo = getPermissionLevelInfo(
                          permission.permission_level,
                        );
                        const isExpired =
                          permission.expires_at &&
                          new Date(permission.expires_at) < new Date();

                        return (
                          <TableRow key={permission.id} hover>
                            <TableCell>
                              <Box display="flex" alignItems="center">
                                {permission.user_id ? (
                                  <>
                                    <PersonIcon
                                      sx={{ mr: 1, color: "primary.main" }}
                                    />
                                    <Box>
                                      <Typography
                                        variant="body2"
                                        fontWeight="medium"
                                      >
                                        {permission.user_email ||
                                          "Unknown User"}
                                      </Typography>
                                      <Typography
                                        variant="caption"
                                        color="text.secondary"
                                      >
                                        User
                                      </Typography>
                                    </Box>
                                  </>
                                ) : (
                                  <>
                                    <GroupIcon
                                      sx={{ mr: 1, color: "secondary.main" }}
                                    />
                                    <Box>
                                      <Typography
                                        variant="body2"
                                        fontWeight="medium"
                                      >
                                        {permission.group_name ||
                                          "Unknown Group"}
                                      </Typography>
                                      <Typography
                                        variant="caption"
                                        color="text.secondary"
                                      >
                                        Group
                                      </Typography>
                                    </Box>
                                  </>
                                )}
                              </Box>
                            </TableCell>
                            <TableCell>
                              <Chip
                                label={levelInfo.label}
                                color={levelInfo.color}
                                size="small"
                                title={levelInfo.description}
                              />
                            </TableCell>
                            <TableCell>
                              <Typography
                                variant="body2"
                                color="text.secondary"
                              >
                                {permission.granter_name || "Unknown"}
                              </Typography>
                            </TableCell>
                            <TableCell>
                              <Typography
                                variant="body2"
                                color="text.secondary"
                              >
                                {formatDate(permission.granted_at)}
                              </Typography>
                            </TableCell>
                            <TableCell>
                              {permission.expires_at ? (
                                <Box display="flex" alignItems="center">
                                  <ScheduleIcon
                                    sx={{
                                      mr: 0.5,
                                      fontSize: 16,
                                      color: isExpired
                                        ? "error.main"
                                        : "warning.main",
                                    }}
                                  />
                                  <Typography
                                    variant="body2"
                                    color={
                                      isExpired
                                        ? "error.main"
                                        : "text.secondary"
                                    }
                                  >
                                    {formatDate(permission.expires_at)}
                                  </Typography>
                                </Box>
                              ) : (
                                <Typography
                                  variant="body2"
                                  color="text.secondary"
                                >
                                  Never
                                </Typography>
                              )}
                            </TableCell>
                            <TableCell>
                              <Chip
                                label={
                                  isExpired
                                    ? "Expired"
                                    : permission.is_active
                                      ? "Active"
                                      : "Inactive"
                                }
                                color={
                                  isExpired
                                    ? "error"
                                    : permission.is_active
                                      ? "success"
                                      : "default"
                                }
                                size="small"
                              />
                            </TableCell>
                            <TableCell align="right">
                              <IconButton
                                onClick={() =>
                                  handleRevokePermission(permission.id)
                                }
                                size="small"
                                color="error"
                                title="Revoke Permission"
                              >
                                <DeleteIcon />
                              </IconButton>
                            </TableCell>
                          </TableRow>
                        );
                      })
                    )}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </CardContent>
        </Card>
      )}
      {/* Grant Permission Dialog */}
      <Dialog
        open={grantDialogOpen}
        onClose={() => setGrantDialogOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Grant Knowledge Base Permission</DialogTitle>
        <DialogContent>
          <Grid container spacing={3} sx={{ mt: 1 }}>
            <Grid item xs={12}>
              <FormControl fullWidth>
                <InputLabel>Target Type</InputLabel>
                <Select
                  value={newPermission.target_type}
                  onChange={(e) =>
                    setNewPermission({
                      ...newPermission,
                      target_type: e.target.value,
                      target_id: "",
                    })
                  }
                  label="Target Type"
                >
                  <MenuItem value="user">
                    <Box display="flex" alignItems="center">
                      <PersonIcon sx={{ mr: 1 }} />
                      User
                    </Box>
                  </MenuItem>
                  <MenuItem value="group">
                    <Box display="flex" alignItems="center">
                      <GroupIcon sx={{ mr: 1 }} />
                      Group
                    </Box>
                  </MenuItem>
                </Select>
              </FormControl>
            </Grid>

            <Grid item xs={12}>
              <Autocomplete
                options={getTargetOptions()
                  // Ensure that only options that aren't already added are shown
                  .filter(
                    (option) =>
                      !permissions.some((p) =>
                        [p.user_id, p.group_id].includes(option.value),
                      ),
                  )}
                getOptionLabel={(option) => option.label}
                value={
                  getTargetOptions().find(
                    (option) => option.value === newPermission.target_id,
                  ) || null
                }
                onChange={(event, newValue) => {
                  setNewPermission({
                    ...newPermission,
                    target_id: newValue?.value || "",
                  });
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={
                      newPermission.target_type === "user"
                        ? "Select User"
                        : "Select Group"
                    }
                    fullWidth
                  />
                )}
              />
            </Grid>

            <Grid item xs={12}>
              <FormControl fullWidth>
                <InputLabel>Permission Level</InputLabel>
                <Select
                  value={newPermission.permission_level}
                  onChange={(e) =>
                    setNewPermission({
                      ...newPermission,
                      permission_level: e.target.value,
                    })
                  }
                  label="Permission Level"
                >
                  {PERMISSION_LEVELS.map((level) => (
                    <MenuItem key={level.value} value={level.value}>
                      <Box>
                        <Typography variant="body2" fontWeight="medium">
                          {level.label}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {level.description}
                        </Typography>
                      </Box>
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>

            <Grid item xs={12}>
              <FormControlLabel
                control={
                  <Switch
                    checked={newPermission.has_expiration}
                    onChange={(e) =>
                      setNewPermission({
                        ...newPermission,
                        has_expiration: e.target.checked,
                      })
                    }
                  />
                }
                label="Set Expiration Date"
              />
            </Grid>

            {newPermission.has_expiration && (
              <Grid item xs={12}>
                <TextField
                  label="Expires At"
                  type="datetime-local"
                  value={newPermission.expires_at || ""}
                  onChange={(e) =>
                    setNewPermission({
                      ...newPermission,
                      expires_at: e.target.value,
                    })
                  }
                  fullWidth
                  InputLabelProps={{
                    shrink: true,
                  }}
                  inputProps={{
                    min: new Date().toISOString().slice(0, 16),
                  }}
                />
              </Grid>
            )}
          </Grid>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setGrantDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleGrantPermission}
            variant="contained"
            disabled={
              !newPermission.target_id || grantPermissionMutation.isLoading
            }
          >
            {grantPermissionMutation.isLoading ? (
              <CircularProgress size={20} />
            ) : (
              "Grant Permission"
            )}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Bulk Operations Dialog */}
      <Dialog
        open={bulkDialogOpen}
        onClose={() => setBulkDialogOpen(false)}
        maxWidth="lg"
        fullWidth
      >
        <DialogTitle>Bulk Permission Operations</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Bulk operations allow you to grant or revoke permissions for
            multiple users or groups at once.
          </Typography>
          <Alert severity="info">
            Bulk operations feature coming soon. For now, use the individual
            grant permission dialog.
          </Alert>
          <Box sx={{ mt: 1 }}>
            <NotImplemented label="Bulk operations not implemented yet" />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setBulkDialogOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default KBPermissions;
