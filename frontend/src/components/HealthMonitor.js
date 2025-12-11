import React from 'react';
import { useQuery } from 'react-query';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Grid,
  Chip,
  Alert,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
} from '@mui/material';
import {
  HealthAndSafety as HealthIcon,
  Storage as DatabaseIcon,
  Api as ApiIcon,
  CheckCircle as SuccessIcon,
  Error as ErrorIcon,
  Warning as WarningIcon,
} from '@mui/icons-material';
import { healthAPI, knowledgeBaseAPI, extractDataFromResponse, extractItemsFromResponse } from '../services/api';
import JSONPretty from 'react-json-pretty';
import 'react-json-pretty/themes/monikai.css';

import { log } from '../utils/log';

function HealthMonitor() {
  // Only call authenticated /health endpoint if user has a token
  const hasToken = Boolean(localStorage.getItem('shu_token'));

  const { data: healthResponse, isLoading: healthLoading } = useQuery(
    'health',
    healthAPI.getHealth,
    {
      refetchInterval: 30000,
      enabled: hasToken,  // Skip if no auth token
    }
  );

  // Extract health data from envelope format
  const health = extractDataFromResponse(healthResponse);

  const { data: readinessResponse, isLoading: readinessLoading } = useQuery(
    'readiness',
    healthAPI.getReadiness,
    { 
      refetchInterval: 30000,
      onSuccess: (data) => {
        log.debug('HealthMonitor - Readiness response:', data);
        const extractedData = extractDataFromResponse(data);
        log.debug('HealthMonitor - Readiness extracted data:', extractedData);
      },
      onError: (error) => {
        log.error('HealthMonitor - Readiness error:', error);
      }
    }
  );

  // Extract readiness data from envelope format
  const readiness = extractDataFromResponse(readinessResponse);

  const { data: livenessResponse, isLoading: livenessLoading } = useQuery(
    'liveness',
    healthAPI.getLiveness,
    { 
      refetchInterval: 30000,
      onSuccess: (data) => {
        log.debug('HealthMonitor - Liveness response:', data);
        const extractedData = extractDataFromResponse(data);
        log.debug('HealthMonitor - Liveness extracted data:', extractedData);
      },
      onError: (error) => {
        log.error('HealthMonitor - Liveness error:', error);
      }
    }
  );

  // Extract liveness data from envelope format
  const liveness = extractDataFromResponse(livenessResponse);

  const { data: databaseResponse, isLoading: dbLoading } = useQuery(
    'database',
    healthAPI.getDatabase,
    { refetchInterval: 30000 }
  );

  // Extract database data from envelope format
  const database = extractDataFromResponse(databaseResponse);

  // Debug query to check knowledge bases
  const { data: knowledgeBasesResponse, isLoading: kbLoading } = useQuery(
    'knowledgeBases',
    knowledgeBaseAPI.list,
    { 
      refetchInterval: 30000,
      onSuccess: (data) => {
        log.debug('HealthMonitor - Knowledge bases response:', data);
        const extractedData = extractDataFromResponse(data);
        log.debug('HealthMonitor - Knowledge bases extracted data:', extractedData);
        const items = extractItemsFromResponse(data);
        log.debug('HealthMonitor - Knowledge bases items:', items);
        log.debug('HealthMonitor - Knowledge bases count:', items?.length);
      },
      onError: (error) => {
        log.error('HealthMonitor - Knowledge bases error:', error);
      }
    }
  );

  // Extract knowledge bases data from envelope format
  const knowledgeBases = extractItemsFromResponse(knowledgeBasesResponse);

  const getStatusColor = (status) => {
    if (!status || typeof status !== 'string') {
      return 'default';
    }
    
    switch (status.toLowerCase()) {
      case 'healthy':
      case 'ready':
      case 'alive':
        return 'success';
      case 'unhealthy':
      case 'not_ready':
      case 'not_alive':
        return 'error';
      default:
        return 'warning';
    }
  };

  const getStatusIcon = (status) => {
    if (!status || typeof status !== 'string') {
      return <WarningIcon color="warning" />;
    }
    
    switch (status.toLowerCase()) {
      case 'healthy':
      case 'ready':
      case 'alive':
        return <SuccessIcon color="success" />;
      case 'unhealthy':
      case 'not_ready':
      case 'not_alive':
        return <ErrorIcon color="error" />;
      default:
        return <WarningIcon color="warning" />;
    }
  };

  const getOverallStatus = () => {
    const healthStatus = health?.status;
    const readinessStatus = readiness?.ready;
    const livenessStatus = liveness?.alive;
    const databaseStatus = database?.status;
    
    const healthyCount = [
      healthStatus === 'healthy',
      readinessStatus === true,
      livenessStatus === true,
      databaseStatus === 'healthy'
    ].filter(Boolean).length;
    
    const totalChecks = 4;
    
    if (healthyCount === totalChecks) return 'healthy';
    if (healthyCount === 0) return 'unhealthy';
    return 'degraded';
  };

  const overallStatus = getOverallStatus();

  if (healthLoading || readinessLoading || livenessLoading || dbLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        System Health Monitor
      </Typography>

      {/* Overall Status */}
      <Alert 
        severity={overallStatus === 'healthy' ? 'success' : overallStatus === 'unhealthy' ? 'error' : 'warning'}
        sx={{ mb: 3 }}
      >
        <Box display="flex" alignItems="center" gap={1}>
          {getStatusIcon(overallStatus)}
          <Typography variant="h6">
            Overall System Status: {overallStatus.toUpperCase()}
          </Typography>
        </Box>
      </Alert>

      {/* Debug Information */}
      <Alert severity="info" sx={{ mb: 3 }}>
        <Typography variant="body2">
          <strong>Debug Info:</strong> Knowledge Bases: {knowledgeBases?.length || 0} | 
          Loading: {kbLoading ? 'Yes' : 'No'} | 
          Response: {knowledgeBasesResponse ? 'Present' : 'Missing'} |
          Items: {knowledgeBases ? 'Present' : 'Missing'} |
          Raw Response Type: {typeof knowledgeBasesResponse} |
          Raw Response Keys: {knowledgeBasesResponse ? Object.keys(knowledgeBasesResponse).join(', ') : 'None'} |
          Readiness: {readiness ? JSON.stringify(readiness) : 'Missing'} |
          Liveness: {liveness ? JSON.stringify(liveness) : 'Missing'}
        </Typography>
      </Alert>

      <Grid container spacing={3}>
        {/* API Health */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" mb={2}>
                <ApiIcon color="primary" sx={{ mr: 1 }} />
                <Typography variant="h6">API Health</Typography>
              </Box>
              <Box display="flex" alignItems="center" justifyContent="space-between" mb={2}>
                <Typography variant="body2" color="text.secondary">
                  Status
                </Typography>
                <Chip
                  label={health?.status || 'Unknown'}
                  color={getStatusColor(health?.status)}
                  size="small"
                />
              </Box>
              {health && (
                <JSONPretty
                  data={health}
                  theme="monokai"
                />
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Database Health */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" mb={2}>
                <DatabaseIcon color="primary" sx={{ mr: 1 }} />
                <Typography variant="h6">Database Health</Typography>
              </Box>
              <Box display="flex" alignItems="center" justifyContent="space-between" mb={2}>
                <Typography variant="body2" color="text.secondary">
                  Status
                </Typography>
                <Chip
                  label={database?.status || 'Unknown'}
                  color={getStatusColor(database?.status)}
                  size="small"
                />
              </Box>
              {database && (
                <JSONPretty
                  data={database}
                  theme="monokai"
                />
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Readiness Probe */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" mb={2}>
                <HealthIcon color="primary" sx={{ mr: 1 }} />
                <Typography variant="h6">Readiness Probe</Typography>
              </Box>
              <Box display="flex" alignItems="center" justifyContent="space-between" mb={2}>
                <Typography variant="body2" color="text.secondary">
                  Status
                </Typography>
                <Chip
                  label={readiness?.ready ? 'Ready' : readiness?.ready === false ? 'Not Ready' : 'Unknown'}
                  color={getStatusColor(readiness?.ready ? 'ready' : readiness?.ready === false ? 'not_ready' : 'unknown')}
                  size="small"
                />
              </Box>
              {readiness && (
                <JSONPretty
                  data={readiness}
                  theme="monokai"
                />
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Liveness Probe */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" mb={2}>
                <HealthIcon color="primary" sx={{ mr: 1 }} />
                <Typography variant="h6">Liveness Probe</Typography>
              </Box>
              <Box display="flex" alignItems="center" justifyContent="space-between" mb={2}>
                <Typography variant="body2" color="text.secondary">
                  Status
                </Typography>
                <Chip
                  label={liveness?.alive ? 'Alive' : liveness?.alive === false ? 'Not Alive' : 'Unknown'}
                  color={getStatusColor(liveness?.alive ? 'alive' : liveness?.alive === false ? 'not_alive' : 'unknown')}
                  size="small"
                />
              </Box>
              {liveness && (
                <JSONPretty
                  data={liveness}
                  theme="monokai"
                />
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Knowledge Bases Status */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" mb={2}>
                <DatabaseIcon color="primary" sx={{ mr: 1 }} />
                <Typography variant="h6">Knowledge Bases Status</Typography>
              </Box>
              
              {kbLoading ? (
                <Box display="flex" justifyContent="center" p={3}>
                  <CircularProgress />
                </Box>
              ) : knowledgeBases && knowledgeBases.length > 0 ? (
                <TableContainer component={Paper}>
                  <Table>
                    <TableHead>
                      <TableRow>
                        <TableCell>Name</TableCell>
                        <TableCell>Status</TableCell>
                        <TableCell>Documents</TableCell>
                        <TableCell>Chunks</TableCell>
                        <TableCell>Last Sync</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {knowledgeBases.map((kb) => (
                        <TableRow key={kb.id}>
                          <TableCell>
                            <Typography variant="body2" fontWeight="medium">
                              {kb.name}
                            </Typography>
                          </TableCell>
                          <TableCell>
                            <Chip
                              label={kb.status || 'unknown'}
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
                            <Typography variant="body2">
                              {kb.last_sync_at ? new Date(kb.last_sync_at).toLocaleDateString() : 'Never'}
                            </Typography>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              ) : (
                <Alert severity="info">
                  No knowledge bases found.
                </Alert>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
}

export default HealthMonitor; 