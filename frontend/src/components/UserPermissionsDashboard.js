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
  Divider,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Avatar,
} from '@mui/material';
import { ExpandMore as ExpandMoreIcon, Group as GroupIcon, CheckCircle as CheckCircleIcon } from '@mui/icons-material';
import { userPermissionsAPI, extractItemsFromResponse, formatError } from '../services/api';

const UserPermissionsDashboard = () => {
  const [error, setError] = useState(null);

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

  const groupMemberships = extractItemsFromResponse(groupsResponse) || [];

  const formatDate = (dateString) => {
    if (!dateString) {
      return 'Never expires';
    }
    return new Date(dateString).toLocaleDateString();
  };

  if (groupsLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <Alert severity="error">Failed to load groups: {error}</Alert>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" component="h1" fontWeight="bold" gutterBottom>
        My Permissions
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        View your group memberships.
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
      </Grid>

      {/* Group Memberships */}
      <Accordion defaultExpanded>
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
