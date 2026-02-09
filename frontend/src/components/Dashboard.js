import React from 'react';
import { useQuery } from 'react-query';
import { Grid, Card, CardContent, Typography, Box, Chip, Button, Alert, CircularProgress } from '@mui/material';
import { Storage as KnowledgeBasesIcon, HealthAndSafety as HealthIcon, Search as QueryIcon } from '@mui/icons-material';
import { healthAPI, knowledgeBaseAPI, extractDataFromResponse, extractItemsFromResponse } from '../services/api';
import { log } from '../utils/log';
import NotImplemented from './NotImplemented';
import { useTheme as useAppTheme } from '../contexts/ThemeContext';
import { getBrandingAppName } from '../utils/constants';

function Dashboard() {
  const {
    data: healthResponse,
    isLoading: healthLoading,
    error: healthError,
  } = useQuery('health', healthAPI.getHealth, { refetchInterval: 30000 });

  const { branding } = useAppTheme();
  const appDisplayName = getBrandingAppName(branding);

  // Extract health data from envelope format
  const health = extractDataFromResponse(healthResponse);

  const {
    data: knowledgeBasesResponse,
    isLoading: kbLoading,
    error: kbError,
  } = useQuery('knowledgeBases', knowledgeBaseAPI.list, {
    refetchInterval: 30000,
    onSuccess: (data) => {
      log.debug('Dashboard - Knowledge bases response:', data);
      const extractedData = extractDataFromResponse(data);
      log.debug('Dashboard - Knowledge bases extracted data:', extractedData);
      const items = extractItemsFromResponse(data);
      log.debug('Dashboard - Knowledge bases items:', items);
      log.debug('Dashboard - Knowledge bases count:', items?.length);
    },
    onError: (error) => {
      log.error('Dashboard - Knowledge bases error:', error);
    },
  });

  // Extract knowledge bases data from envelope format
  const knowledgeBasesData = extractDataFromResponse(knowledgeBasesResponse);
  const knowledgeBases = extractItemsFromResponse(knowledgeBasesResponse);

  const getStatusColor = (status) => {
    if (!status || typeof status !== 'string') {
      return 'default';
    }

    switch (status.toLowerCase()) {
      case 'healthy':
      case 'ready':
        return 'success';
      case 'unhealthy':
      case 'not_ready':
        return 'error';
      default:
        return 'warning';
    }
  };

  const getStatusIcon = (status) => {
    if (!status || typeof status !== 'string') {
      return '🟡';
    }

    switch (status.toLowerCase()) {
      case 'healthy':
      case 'ready':
        return '🟢';
      case 'unhealthy':
      case 'not_ready':
        return '🔴';
      default:
        return '🟡';
    }
  };

  // Calculate totals from knowledge bases
  const totalDocuments = knowledgeBases?.reduce((sum, kb) => sum + (kb.document_count || 0), 0) || 0;
  const totalChunks = knowledgeBases?.reduce((sum, kb) => sum + (kb.total_chunks || 0), 0) || 0;

  if (healthLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        {appDisplayName} Admin Dashboard
      </Typography>

      {healthError && (
        <Alert severity="error" sx={{ mb: 3 }}>
          Unable to connect to Shu API. Please check if the service is running.
        </Alert>
      )}

      {/* Debug Information */}
      {process.env.NODE_ENV !== 'production' && (
        <Alert severity="info" sx={{ mb: 3 }}>
          <Typography variant="body2">
            <strong>Debug Info:</strong> Knowledge Bases: {knowledgeBases?.length || 0} | Loading:{' '}
            {kbLoading ? 'Yes' : 'No'} | Error: {kbError ? 'Yes' : 'No'} | Response:{' '}
            {knowledgeBasesResponse ? 'Present' : 'Missing'} | Items: {knowledgeBases ? 'Present' : 'Missing'} | Total:{' '}
            {knowledgeBasesData?.total || 0} | Raw Response Type: {typeof knowledgeBasesResponse} | Raw Response Keys:{' '}
            {knowledgeBasesResponse ? Object.keys(knowledgeBasesResponse).join(', ') : 'None'}
          </Typography>
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* System Health */}
        <Grid item xs={12} md={6} lg={3}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" mb={2}>
                <HealthIcon color="primary" sx={{ mr: 1 }} />
                <Typography variant="h6">System Health</Typography>
              </Box>
              <Box display="flex" alignItems="center" justifyContent="space-between">
                <Typography variant="body2" color="text.secondary">
                  API Status
                </Typography>
                <Chip label={health?.status || 'Unknown'} color={getStatusColor(health?.status)} size="small" />
              </Box>
              <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                {getStatusIcon(health?.status)} {health?.status || 'Unknown'}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Knowledge Bases */}
        <Grid item xs={12} md={6} lg={3}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" mb={2}>
                <KnowledgeBasesIcon color="primary" sx={{ mr: 1 }} />
                <Typography variant="h6">Knowledge Bases</Typography>
              </Box>
              <Typography variant="h4" color="primary">
                {kbLoading ? '...' : knowledgeBases?.length || 0}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Total Knowledge Bases
              </Typography>
              <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                {totalDocuments} documents, {totalChunks} chunks
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Query Stats */}
        <Grid item xs={12} md={6} lg={3}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" mb={2}>
                <QueryIcon color="primary" sx={{ mr: 1 }} />
                <Typography variant="h6">Query Stats</Typography>
              </Box>
              <Box sx={{ mb: 1 }}>
                <NotImplemented label="Global status panel not fully wired" />
              </Box>
              <Typography variant="h4" color="primary">
                N/A
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Total Queries Today
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Quick Actions */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Quick Actions
              </Typography>
              <Box display="flex" gap={2} flexWrap="wrap">
                <Button
                  variant="contained"
                  color="secondary"
                  onClick={() => (window.location.href = '/admin/briefing')}
                >
                  Run Morning Briefing (Demo/Experimental)
                </Button>
              </Box>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                Experimental/Demo feature — requires Gmail, Google Drive, and Google Calendar plugins to be installed
                and authorized. This is a hard-coded test of the future "Experience Creator" feature.
                <Typography variant="body1" color="text.secondary">
                  The Experience Creator will allow:
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  - Run one or more plugin ops with parameters
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  - Assemble plugin outputs into a prompt via a template
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  - Execute an LLM provider with that prompt
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  - Produce a runnable, saveable, reusable experience (with run history)
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  - And later, combine with agents and workflows to create automations
                </Typography>
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
}

export default Dashboard;
