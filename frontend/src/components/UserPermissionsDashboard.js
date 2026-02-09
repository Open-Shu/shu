import React, { useState } from 'react';
import { useQuery } from 'react-query';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Alert,
  CircularProgress,
  Grid,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Divider,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Avatar,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  Security as SecurityIcon,
  Group as GroupIcon,
  Person as PersonIcon,
  Schedule as ScheduleIcon,
  CheckCircle as CheckCircleIcon,
  Info as InfoIcon,
  Storage as StorageIcon,
} from '@mui/icons-material';
import { userPermissionsAPI, extractItemsFromResponse, formatError } from '../services/api';

const PERMISSION_LEVELS = [
  {
    value: 'owner',
    label: 'Owner',
    description: 'Full control, can delete KB, manage permissions',
    color: 'error',
  },
  {
    value: 'admin',
    label: 'Admin',
    description: 'Can modify KB, add/remove documents, manage members',
    color: 'warning',
  },
  {
    value: 'member',
    label: 'Member',
    description: 'Can query KB, view documents, add documents',
    color: 'primary',
  },
  {
    value: 'read_only',
    label: 'Read Only',
    description: 'Can only query KB, no modifications',
    color: 'default',
  },
];

const UserPermissionsDashboard = () => {
  const [error, setError] = useState(null);

  // Fetch current user's KB permissions
  const { data: kbPermissionsResponse, isLoading: kbLoading } = useQuery(
    'currentUserKBPermissions',
    userPermissionsAPI.getCurrentUserKBPermissions,
    {
      onError: (err) => {
        setError(formatError(err).message);
      },
    }
  );

  // Fetch current user's group memberships
  const { data: groupsResponse, isLoading: groupsLoading } = useQuery(
    'currentUserGroups',
    userPermissionsAPI.getCurrentUserGroups,
    {
      onError: (err) => {
        setError(formatError(err).message);
      },
    }
  );

  const kbPermissions = extractItemsFromResponse(kbPermissionsResponse) || [];
  const groupMemberships = extractItemsFromResponse(groupsResponse) || [];

  const getPermissionLevelInfo = (level) => {
    return PERMISSION_LEVELS.find((p) => p.value === level) || PERMISSION_LEVELS[3];
  };

  const formatDate = (dateString) => {
    if (!dateString) {
      return 'Never expires';
    }
    return new Date(dateString).toLocaleDateString();
  };

  const getPermissionSummary = () => {
    const summary = {
      total: kbPermissions.length,
      owner: 0,
      admin: 0,
      member: 0,
      read_only: 0,
      expiring_soon: 0,
    };

    const now = new Date();
    const thirtyDaysFromNow = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);

    kbPermissions.forEach((permission) => {
      summary[permission.permission_level]++;

      if (permission.expires_at) {
        const expiryDate = new Date(permission.expires_at);
        if (expiryDate <= thirtyDaysFromNow) {
          summary.expiring_soon++;
        }
      }
    });

    return summary;
  };

  if (kbLoading || groupsLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  const summary = getPermissionSummary();

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" component="h1" fontWeight="bold" gutterBottom>
        My Permissions
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        View your access permissions across knowledge bases and group memberships.
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {/* Summary Cards */}
      <Grid container spacing={3} sx={{ mb: 4 }}>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center">
                <StorageIcon color="primary" sx={{ mr: 2 }} />
                <Box>
                  <Typography variant="h4" fontWeight="bold">
                    {summary.total}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Knowledge Bases
                  </Typography>
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center">
                <GroupIcon color="primary" sx={{ mr: 2 }} />
                <Box>
                  <Typography variant="h4" fontWeight="bold">
                    {groupMemberships.length}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Group Memberships
                  </Typography>
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center">
                <SecurityIcon color="warning" sx={{ mr: 2 }} />
                <Box>
                  <Typography variant="h4" fontWeight="bold">
                    {summary.owner + summary.admin}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Admin Access
                  </Typography>
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center">
                <ScheduleIcon color={summary.expiring_soon > 0 ? 'error' : 'success'} sx={{ mr: 2 }} />
                <Box>
                  <Typography variant="h4" fontWeight="bold">
                    {summary.expiring_soon}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Expiring Soon
                  </Typography>
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Knowledge Base Permissions */}
      <Accordion defaultExpanded>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Box display="flex" alignItems="center">
            <StorageIcon sx={{ mr: 2, color: 'primary.main' }} />
            <Typography variant="h6" fontWeight="medium">
              Knowledge Base Permissions ({kbPermissions.length})
            </Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          {kbPermissions.length === 0 ? (
            <Box textAlign="center" py={4}>
              <InfoIcon sx={{ fontSize: 48, color: 'text.secondary', mb: 2 }} />
              <Typography variant="body1" color="text.secondary">
                You don't have access to any knowledge bases yet.
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Contact your administrator to request access.
              </Typography>
            </Box>
          ) : (
            <TableContainer component={Paper} variant="outlined">
              <Table>
                <TableHead>
                  <TableRow>
                    <TableCell>Knowledge Base</TableCell>
                    <TableCell>Permission Level</TableCell>
                    <TableCell>Source</TableCell>
                    <TableCell>Expires</TableCell>
                    <TableCell>Granted</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {kbPermissions.map((permission) => {
                    const levelInfo = getPermissionLevelInfo(permission.permission_level);
                    const isExpiringSoon =
                      permission.expires_at &&
                      new Date(permission.expires_at) <= new Date(Date.now() + 30 * 24 * 60 * 60 * 1000);

                    return (
                      <TableRow key={permission.id} hover>
                        <TableCell>
                          <Box display="flex" alignItems="center">
                            <StorageIcon sx={{ mr: 1, color: 'primary.main' }} />
                            <Typography variant="body2" fontWeight="medium">
                              {permission.kb_name || permission.knowledge_base_id}
                            </Typography>
                          </Box>
                        </TableCell>
                        <TableCell>
                          <Chip label={levelInfo.label} color={levelInfo.color} size="small" icon={<SecurityIcon />} />
                        </TableCell>
                        <TableCell>
                          <Box display="flex" alignItems="center">
                            {permission.group_id ? (
                              <>
                                <GroupIcon sx={{ mr: 1, fontSize: 16 }} />
                                <Typography variant="body2">
                                  Group: {permission.group_name || permission.group_id}
                                </Typography>
                              </>
                            ) : (
                              <>
                                <PersonIcon sx={{ mr: 1, fontSize: 16 }} />
                                <Typography variant="body2">Direct</Typography>
                              </>
                            )}
                          </Box>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color={isExpiringSoon ? 'error' : 'text.secondary'}>
                            {formatDate(permission.expires_at)}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {formatDate(permission.granted_at)}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </AccordionDetails>
      </Accordion>

      {/* Group Memberships */}
      <Accordion sx={{ mt: 2 }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Box display="flex" alignItems="center">
            <GroupIcon sx={{ mr: 2, color: 'primary.main' }} />
            <Typography variant="h6" fontWeight="medium">
              Group Memberships ({groupMemberships.length})
            </Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          {groupMemberships.length === 0 ? (
            <Box textAlign="center" py={4}>
              <GroupIcon sx={{ fontSize: 48, color: 'text.secondary', mb: 2 }} />
              <Typography variant="body1" color="text.secondary">
                You're not a member of any groups yet.
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Group memberships provide access to shared knowledge bases.
              </Typography>
            </Box>
          ) : (
            <List>
              {groupMemberships.map((membership, index) => (
                <React.Fragment key={membership.id}>
                  <ListItem>
                    <ListItemIcon>
                      <Avatar sx={{ bgcolor: 'primary.main' }}>
                        <GroupIcon />
                      </Avatar>
                    </ListItemIcon>
                    <ListItemText
                      primary={
                        <Box display="flex" alignItems="center" gap={1}>
                          <Typography variant="body1" fontWeight="medium">
                            {membership.group_name || membership.group_id}
                          </Typography>
                          <Chip label={membership.role || 'Member'} size="small" variant="outlined" />
                          {membership.is_active && <CheckCircleIcon color="success" sx={{ fontSize: 16 }} />}
                        </Box>
                      }
                      secondary={
                        <Typography variant="body2" color="text.secondary">
                          Joined: {formatDate(membership.granted_at)}
                          {membership.group_description && ` • ${membership.group_description}`}
                        </Typography>
                      }
                    />
                  </ListItem>
                  {index < groupMemberships.length - 1 && <Divider />}
                </React.Fragment>
              ))}
            </List>
          )}
        </AccordionDetails>
      </Accordion>
    </Box>
  );
};

export default UserPermissionsDashboard;
