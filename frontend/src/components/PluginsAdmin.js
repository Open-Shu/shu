import React, { useMemo, useState } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Stack,
  Switch,
  Tooltip,
  Typography,
  Collapse,
  Grid,
  Avatar,
  Badge,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutline';
import SyncIcon from '@mui/icons-material/Sync';
import InputIcon from '@mui/icons-material/Input';
import OutputIcon from '@mui/icons-material/Output';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import SettingsIcon from '@mui/icons-material/Settings';
import SecurityIcon from '@mui/icons-material/Security';
import CodeIcon from '@mui/icons-material/Code';
import DeleteIcon from '@mui/icons-material/Delete';
import JSONPretty from 'react-json-pretty';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { extractDataFromResponse, formatError } from '../services/api';
import { pluginsAPI } from '../services/pluginsApi';
import PluginExecutionModal from './PluginExecutionModal';
import PluginLimitsEditor from './PluginLimitsEditor';
import LimitsStatsPanel from './LimitsStatsPanel';
import PluginSecretsEditor from './PluginSecretsEditor';
import PluginUploadDialog from './PluginUploadDialog';
import PageHelpHeader from './PageHelpHeader';
import ExtensionIcon from '@mui/icons-material/Extension';

const PluginCard = ({ plugin, onToggleEnabled, onExecute, onExpandSchema, onExpandLimits, onExpandSecrets, onDelete, expanded, limitsExpanded, secretsExpanded, isLoading }) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const menuOpen = Boolean(anchorEl);

  const handleMenuClick = (event) => {
    setAnchorEl(event.currentTarget);
  };

  const handleMenuClose = () => {
    setAnchorEl(null);
  };

  const handleMenuAction = (action) => {
    handleMenuClose();
    action();
  };

  // Helper function to get capability tooltips
  const getCapabilityTooltip = (capability) => {
    const tooltips = {
      'auth': 'Plugin can authenticate users and manage authentication tokens',
      'storage': 'Plugin can store and retrieve data persistently',
      'http': 'Plugin can make HTTP requests to external services',
      'file': 'Plugin can read and write files',
      'email': 'Plugin can send and receive emails',
      'calendar': 'Plugin can access and manage calendar events',
      'search': 'Plugin can perform search operations',
      'ai': 'Plugin can interact with AI/ML services',
      'database': 'Plugin can connect to and query databases',
      'webhook': 'Plugin can receive and process webhook notifications',
      'oauth': 'Plugin supports OAuth authentication flows',
      'encryption': 'Plugin can encrypt and decrypt data',
      'notification': 'Plugin can send notifications to users',
      'scheduling': 'Plugin can schedule and manage recurring tasks',
      'monitoring': 'Plugin can monitor system health and metrics',
      'identity': 'Plugin can access user identity information and profile data',
      'kb': 'Plugin can interact with knowledge bases for storing and retrieving documents',
      'secrets': 'Plugin can access and manage encrypted user secrets and credentials',
      'cache': 'Plugin can use caching mechanisms for improved performance',
    };
    return tooltips[capability] || `Host capability: ${capability}`;
  };

  // Generate plugin avatar from name
  const getPluginAvatar = (name) => {
    const colors = ['#1976d2', '#388e3c', '#f57c00', '#7b1fa2', '#d32f2f', '#0288d1', '#689f38', '#fbc02d'];
    const colorIndex = name.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0) % colors.length;
    return {
      bgcolor: colors[colorIndex],
      color: 'white',
      children: name.charAt(0).toUpperCase() + (name.split('_')[1]?.[0]?.toUpperCase() || name.charAt(1)?.toUpperCase() || ''),
    };
  };

  const enumOps = Array.isArray(plugin?.input_schema?.properties?.op?.enum)
    ? plugin.input_schema.properties.op.enum
    : [];
  const allowed = Array.isArray(plugin.allowed_feed_ops) ? plugin.allowed_feed_ops : [];
  const defaultOp = plugin.default_feed_op || null;
  const ops = (enumOps && enumOps.length) ? enumOps : allowed;

  return (
    <Card
      sx={{
        transition: 'all 0.2s ease-in-out',
        '&:hover': {
          boxShadow: 2,
          transform: 'translateY(-1px)',
        },
        opacity: plugin.enabled ? 1 : 0.7,
      }}
    >
      <CardContent sx={{ pb: 2 }}>
        <Grid container spacing={2} alignItems="center">
          {/* Plugin Avatar & Basic Info */}
          <Grid item xs={12} sm={6} md={4}>
            <Stack direction="row" spacing={2} alignItems="flex-start">
              <Badge
                overlap="circular"
                anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
                badgeContent={
                  <Box
                    sx={{
                      width: 12,
                      height: 12,
                      borderRadius: '50%',
                      bgcolor: plugin.enabled ? 'success.main' : 'grey.400',
                      border: '2px solid white',
                    }}
                  />
                }
              >
                <Avatar {...getPluginAvatar(plugin.name)} sx={{ width: 40, height: 40 }} />
              </Badge>
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                  <Typography variant="h6" sx={{ fontWeight: 600 }}>
                    {plugin.display_name || plugin.name}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    v{plugin.version || 'n/a'}
                  </Typography>
                </Stack>
                {Array.isArray(plugin.capabilities) && plugin.capabilities.length > 0 && (
                  <Box sx={{ mb: 1 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                      Host Capabilities
                    </Typography>
                    <Stack direction="row" spacing={0.5} alignItems="center" sx={{ flexWrap: 'wrap', gap: 0.5, minHeight: 20 }}>
                      {plugin.capabilities.slice(0, 2).map((c) => (
                        <Tooltip key={c} title={getCapabilityTooltip(c)} arrow>
                          <Chip size="small" label={c} variant="outlined" sx={{ fontSize: '0.7rem', height: 20, lineHeight: 1 }} />
                        </Tooltip>
                      ))}
                      {plugin.capabilities.length > 2 && (
                        <Tooltip
                          title={
                            <Stack spacing={1}>
                              <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                                Additional Host Capabilities:
                              </Typography>
                              {plugin.capabilities.slice(2).map((c) => (
                                <Box key={c}>
                                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                                    {c}
                                  </Typography>
                                  <Typography variant="body2" color="text.secondary">
                                    {getCapabilityTooltip(c)}
                                  </Typography>
                                </Box>
                              ))}
                            </Stack>
                          }
                          arrow
                          componentsProps={{
                            tooltip: {
                              sx: {
                                bgcolor: 'grey.900',
                                color: 'white',
                                fontSize: '0.875rem',
                                maxWidth: 300,
                                p: 2,
                              }
                            }
                          }}
                        >
                          <Chip size="small" label={`+${plugin.capabilities.length - 2}`} variant="outlined" sx={{ fontSize: '0.7rem', height: 20, lineHeight: 1 }} />
                        </Tooltip>
                      )}
                    </Stack>
                  </Box>
                )}
              </Box>
            </Stack>
          </Grid>

          {/* Operations */}
          <Grid item xs={12} sm={6} md={4}>
            {ops && ops.length > 0 && (
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                  Operations
                </Typography>
                <Stack direction="row" spacing={0.5} alignItems="center" sx={{ flexWrap: 'wrap', gap: 0.5, minHeight: 20 }}>
                  {ops.slice(0, 3).map((op) => {
                    const feedSafe = allowed.includes(op);
                    const isDefault = defaultOp === op;
                    const opDef = plugin?.input_schema?.properties?.op || {};
                    const xui = (opDef['x-ui'] || opDef['x_ui']) || {};
                    const enumLabels = xui.enum_labels || {};
                    const enumHelp = xui.enum_help || {};
                    const label = enumLabels[String(op)] || String(op);
                    const help = enumHelp[String(op)];
                    const chip = (
                      <Chip
                        key={op}
                        size="small"
                        label={label}
                        variant={isDefault ? 'filled' : 'outlined'}
                        color={isDefault ? 'primary' : (feedSafe ? 'success' : 'default')}
                        sx={{ fontSize: '0.7rem', height: 20, lineHeight: 1 }}
                      />
                    );
                    return help ? (
                      <Tooltip key={op} title={help} arrow>
                        <span>{chip}</span>
                      </Tooltip>
                    ) : chip;
                  })}
                  {ops.length > 3 && (
                    <Tooltip
                      title={
                        <Stack spacing={1}>
                          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                            Additional Operations:
                          </Typography>
                          {ops.slice(3).map((op) => {
                            const opDef = plugin?.input_schema?.properties?.op || {};
                            const xui = (opDef['x-ui'] || opDef['x_ui']) || {};
                            const enumLabels = xui.enum_labels || {};
                            const enumHelp = xui.enum_help || {};
                            const label = enumLabels[String(op)] || String(op);
                            const help = enumHelp[String(op)] || 'No description available';
                            return (
                              <Box key={op}>
                                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                                  {label}
                                </Typography>
                                <Typography variant="body2" color="text.secondary">
                                  {help}
                                </Typography>
                              </Box>
                            );
                          })}
                        </Stack>
                      }
                      arrow
                      componentsProps={{
                        tooltip: {
                          sx: {
                            bgcolor: 'grey.900',
                            color: 'white',
                            fontSize: '0.875rem',
                            maxWidth: 300,
                            p: 2,
                          }
                        }
                      }}
                    >
                      <Chip size="small" label={`+${ops.length - 3}`} variant="outlined" sx={{ fontSize: '0.7rem', height: 20, lineHeight: 1 }} />
                    </Tooltip>
                  )}
                </Stack>
              </Box>
            )}
          </Grid>

          {/* Schema & Controls */}
          <Grid item xs={12} md={4}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="flex-end">
              {/* Schema indicators */}
              <Stack direction="row" spacing={0.5} alignItems="center">
                {plugin.input_schema && (
                  <Tooltip title="Has input schema">
                    <Chip
                      size="small"
                      icon={<InputIcon sx={{ fontSize: 14 }} />}
                      label="In"
                      variant="outlined"
                      color="success"
                      sx={{ fontSize: '0.7rem', height: 24 }}
                    />
                  </Tooltip>
                )}
                {plugin.output_schema && (
                  <Tooltip title="Has output schema">
                    <Chip
                      size="small"
                      icon={<OutputIcon sx={{ fontSize: 14 }} />}
                      label="Out"
                      variant="outlined"
                      color="info"
                      sx={{ fontSize: '0.7rem', height: 24 }}
                    />
                  </Tooltip>
                )}
              </Stack>

              {/* Enable/Disable Switch */}
              <Tooltip title={`${plugin.enabled ? 'Disable' : 'Enable'} this plugin`}>
                <Switch
                  size="small"
                  checked={!!plugin.enabled}
                  onChange={(e) => onToggleEnabled(plugin.name, e.target.checked)}
                  disabled={isLoading}
                />
              </Tooltip>

              {/* Execute Button */}
              <Tooltip title="Execute this plugin">
                <span>
                  <IconButton
                    size="small"
                    color="primary"
                    onClick={() => onExecute(plugin)}
                    disabled={!plugin.enabled}
                    sx={{
                      bgcolor: plugin.enabled ? 'primary.50' : 'transparent',
                      '&:hover': { bgcolor: 'primary.100' }
                    }}
                  >
                    <PlayCircleOutlineIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>

              {/* More Actions Menu */}
              <Tooltip title="More actions">
                <IconButton
                  size="small"
                  onClick={handleMenuClick}
                  sx={{ color: 'text.secondary' }}
                >
                  <MoreVertIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Stack>
          </Grid>
        </Grid>

        {/* Expandable Sections */}
        <Collapse in={!!expanded} timeout="auto" unmountOnExit>
          <Divider sx={{ my: 2 }} />
          <Box>
            <Typography variant="subtitle2" gutterBottom>Input Schema</Typography>
            <Box sx={{ bgcolor: 'grey.50', p: 2, borderRadius: 1, border: '1px solid', borderColor: 'grey.200', mb: 2 }}>
              <JSONPretty data={plugin.input_schema || {}} />
            </Box>
            <Typography variant="subtitle2" gutterBottom>Output Schema</Typography>
            <Box sx={{ bgcolor: 'grey.50', p: 2, borderRadius: 1, border: '1px solid', borderColor: 'grey.200' }}>
              <JSONPretty data={plugin.output_schema || {}} />
            </Box>
          </Box>
        </Collapse>

        <Collapse in={!!limitsExpanded} timeout="auto" unmountOnExit>
          <Divider sx={{ my: 2 }} />
          <Box>
            <PluginLimitsEditor name={plugin.name} />
          </Box>
        </Collapse>

        <Collapse in={!!secretsExpanded} timeout="auto" unmountOnExit>
          <Divider sx={{ my: 2 }} />
          <Box>
            <PluginSecretsEditor name={plugin.name} />
          </Box>
        </Collapse>
      </CardContent>

      {/* Actions Menu */}
      <Menu
        anchorEl={anchorEl}
        open={menuOpen}
        onClose={handleMenuClose}
        transformOrigin={{ horizontal: 'right', vertical: 'top' }}
        anchorOrigin={{ horizontal: 'right', vertical: 'bottom' }}
      >
        <MenuItem onClick={() => handleMenuAction(() => onExpandSchema(plugin.name))}>
          <ListItemIcon>
            <CodeIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{expanded ? 'Hide Schema' : 'Show Schema'}</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => handleMenuAction(() => onExpandLimits(plugin.name))}>
          <ListItemIcon>
            <SettingsIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Configure Limits</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => handleMenuAction(() => onExpandSecrets(plugin.name))}>
          <ListItemIcon>
            <SecurityIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Manage Secrets</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem onClick={() => handleMenuAction(() => onDelete(plugin.name))}>
          <ListItemIcon>
            <DeleteIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText sx={{ color: 'error.main' }}>Delete Plugin</ListItemText>
        </MenuItem>
      </Menu>
    </Card>
  );
};

export default function PluginsAdmin() {
  const qc = useQueryClient();
  const [execPlugin, setExecPlugin] = useState(null);
  const [expanded, setExpanded] = useState({});
  const [limitsExpanded, setLimitsExpanded] = useState({});
  const [secretsExpanded, setSecretsExpanded] = useState({});
  const [uploadOpen, setUploadOpen] = useState(false);
  const { data, isLoading, isFetching, error, refetch } = useQuery(
    ['plugins', 'list'],
    () => pluginsAPI.list().then(extractDataFromResponse),
    { staleTime: 5000 }
  );

  const plugins = useMemo(() => {
    const raw = Array.isArray(data) ? data : [];
    // Sort by enabled status first, then by name
    return raw.sort((a, b) => {
      if (a.enabled !== b.enabled) {
        return b.enabled - a.enabled; // enabled plugins first
      }
      const an = (a.display_name || a.name || '').toLowerCase();
      const bn = (b.display_name || b.name || '').toLowerCase();
      return an.localeCompare(bn);
    });
  }, [data]);

  const enableMut = useMutation(
    ({ name, enabled }) => pluginsAPI.setEnabled(name, enabled).then(extractDataFromResponse),
    {
      onSuccess: () => { qc.invalidateQueries(['plugins', 'list']); },
    }
  );

  const syncMut = useMutation(
    () => pluginsAPI.sync().then(extractDataFromResponse),
    {
      onSuccess: () => { qc.invalidateQueries(['plugins', 'list']); },
    }
  );

  const handleToggleEnabled = (name, enabled) => {
    enableMut.mutate({ name, enabled });
  };

  const handleExecute = (plugin) => {
    setExecPlugin(plugin);
  };

  const handleExpandSchema = (name) => {

    setExpanded((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const handleExpandLimits = (name) => {
    setLimitsExpanded((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const handleExpandSecrets = (name) => {
    setSecretsExpanded((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const enabledCount = plugins.filter(t => t.enabled).length;
  const totalCount = plugins.length;

  const deleteMut = useMutation(
    (name) => pluginsAPI.deletePlugin(name).then(extractDataFromResponse),
    { onSuccess: () => { qc.invalidateQueries(['plugins', 'list']); } }
  );

  const onDelete = (name) => {
    if (!window.confirm('Delete this plugin and remove its files from the server?')) return;
    deleteMut.mutate(name);
  };

  return (
    <Box p={3}>
      <PageHelpHeader
        title="Plugins"
        description="Plugins extend your assistant with capabilities like email, calendar, cloud storage, and more. Enable plugins here, then users can connect their accounts in Settings > Connected Accounts. Plugins can also power automated Plugin Feeds to ingest data into knowledge bases."
        icon={<ExtensionIcon />}
        tips={[
          'Enable a plugin first, then users need to authorize their accounts in Connected Accounts',
          'Each plugin lists its required OAuth scopes and capabilities',
          'Use the menu (three dots) to configure limits or manage secrets for each plugin',
          'Click Sync Plugins after adding new plugin packages to the server',
          'Plugins with feeds capability can be used in Plugin Feeds for automated data sync',
        ]}
      />
      {/* Header */}
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={3}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            {enabledCount} of {totalCount} plugins enabled
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Tooltip title="Upload a plugin package (.zip/.tgz) to install into the server">
            <span>
              <Button
                variant="contained"
                onClick={() => setUploadOpen(true)}
                sx={{ minWidth: 140 }}
              >
                Upload Plugin
              </Button>
            </span>
          </Tooltip>
          <Tooltip title="Reload the plugin list from the server">
            <span>
              <Button
                variant="outlined"
                startIcon={<RefreshIcon />}
                onClick={() => refetch()}
                disabled={isFetching}
                sx={{ minWidth: 100 }}
              >
                Refresh
              </Button>
            </span>
          </Tooltip>
          <Tooltip title="Re-scan plugin manifests and update the Plugin Registry">
            <span>
              <Button
                variant="contained"
                startIcon={<SyncIcon />}
                onClick={() => syncMut.mutate()}
                disabled={syncMut.isLoading}
                sx={{ minWidth: 120 }}
              >
                Sync Plugins
              </Button>
            </span>
          </Tooltip>
        </Stack>
      </Stack>

      {/* Debug panel - show only in non-production AND when explicitly enabled */}
      {process.env.NODE_ENV !== 'production' && process.env.REACT_APP_SHOW_LIMIT_STATS === '1' && (
        <Box mb={3}>
          <LimitsStatsPanel />
        </Box>
      )}

      {/* Loading State */}
      {isLoading && (
        <Box display="flex" alignItems="center" justifyContent="center" py={8}>
          <Stack alignItems="center" spacing={2}>
            <CircularProgress size={40} />
            <Typography variant="body2" color="text.secondary">Loading plugins...</Typography>
          </Stack>
        </Box>
      )}

      {/* Error State */}
      {error && (
        <Box
          sx={{
            bgcolor: 'error.50',
            border: '1px solid',
            borderColor: 'error.200',
            borderRadius: 1,
            p: 2,
            mb: 3
          }}
        >
          <Typography color="error.main">{formatError(error)}</Typography>
        </Box>
      )}

      {/* Plugins Grid */}
      {!isLoading && !error && (
        <Stack spacing={2}>
          {plugins.length === 0 ? (
            <Box
              sx={{
                textAlign: 'center',
                py: 8,
                bgcolor: 'grey.50',
                borderRadius: 2,
                border: '1px dashed',
                borderColor: 'grey.300'
              }}
            >
              <Typography variant="h6" color="text.secondary" gutterBottom>
                No plugins found
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Try syncing plugins to discover available plugins
              </Typography>
              <Button
                variant="contained"
                startIcon={<SyncIcon />}
                onClick={() => syncMut.mutate()}
                disabled={syncMut.isLoading}
              >
                Sync Plugins
              </Button>
            </Box>
          ) : (
            plugins.map((plugin) => (
              <PluginCard
                key={plugin.name}
                plugin={plugin}
                onToggleEnabled={handleToggleEnabled}
                onExecute={handleExecute}
                onExpandSchema={handleExpandSchema}
                onExpandLimits={handleExpandLimits}
                onExpandSecrets={handleExpandSecrets}
                onDelete={onDelete}
                expanded={expanded[plugin.name]}
                limitsExpanded={limitsExpanded[plugin.name]}
                secretsExpanded={secretsExpanded[plugin.name]}
                isLoading={enableMut.isLoading}
              />
            ))
          )}
        </Stack>
      )}

      <PluginExecutionModal
        open={!!execPlugin}
        plugin={execPlugin}
        onClose={() => setExecPlugin(null)}
      />
      <PluginUploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => {
          setUploadOpen(false);
          syncMut.mutate();
        }}
      />
    </Box>
  );
}

