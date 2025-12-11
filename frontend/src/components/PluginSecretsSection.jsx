import React, { useMemo, useState } from 'react';
import { Box, Typography, Paper, Divider, Stack, Button, TextField, IconButton, Collapse, Tooltip } from '@mui/material';
import { useQueries, useMutation, useQueryClient } from 'react-query';
import DeleteIcon from '@mui/icons-material/Delete';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import { pluginsAPI } from '../services/pluginsApi';
import { extractDataFromResponse, formatError } from '../services/api';
import HelpTooltip from './HelpTooltip.jsx';
import { extractUserConfigurableSecretKeys } from '../utils/pluginSecrets';

/**
 * Plugin Secrets Section component for Connected Accounts page.
 * Displays and manages user-configurable secrets for plugins.
 * 
 * @param {Object} props
 * @param {Array} props.plugins - Array of plugin objects from the plugins API
 * @param {Function} props.onSuccess - Callback when a secret is saved/deleted successfully
 * @param {Function} props.onError - Callback when an operation fails
 */
export default function PluginSecretsSection({ plugins, onSuccess, onError }) {
  const qc = useQueryClient();
  const [expandedPlugin, setExpandedPlugin] = useState(null);
  const [secretInputs, setSecretInputs] = useState({});

  // Compute plugins with secret requirements from op_auth[op].secrets
  const pluginsWithSecrets = useMemo(() => {
    const pluginList = Array.isArray(plugins) ? plugins : [];
    const result = [];
    for (const p of pluginList) {
      const secretKeys = extractUserConfigurableSecretKeys(p?.op_auth);
      if (secretKeys.size > 0) {
        result.push({
          name: p.name,
          label: p.display_name || p.name,
          requiredKeys: Array.from(secretKeys).sort(),
        });
      }
    }
    return result;
  }, [plugins]);

  // Query user's secrets for plugins with requirements
  const secretsQueriesArr = useQueries(
    pluginsWithSecrets.map((pl) => ({
      queryKey: ['plugins', 'selfSecrets', pl.name],
      queryFn: () => pluginsAPI.listSelfSecrets(pl.name).then(extractDataFromResponse),
      enabled: !!pl.name,
    }))
  );
  const secretsQueries = useMemo(
    () => Object.fromEntries(pluginsWithSecrets.map((pl, idx) => [pl.name, secretsQueriesArr[idx]])),
    [pluginsWithSecrets, secretsQueriesArr]
  );

  // Mutation for setting a secret
  const setSecretMut = useMutation(
    ({ pluginName, key, value }) => pluginsAPI.setSelfSecret(pluginName, key, value).then(extractDataFromResponse),
    {
      onSuccess: (_data, vars) => {
        qc.invalidateQueries(['plugins', 'selfSecrets', vars.pluginName]);
        setSecretInputs((prev) => ({ ...prev, [vars.pluginName]: { ...(prev[vars.pluginName] || {}), [vars.key]: '' } }));
        onSuccess?.('Secret saved');
      },
      onError: (e) => {
        onError?.(`Failed to save secret: ${formatError(e)}`);
      },
    }
  );

  // Mutation for deleting a secret
  const deleteSecretMut = useMutation(
    ({ pluginName, key }) => pluginsAPI.deleteSelfSecret(pluginName, key).then(extractDataFromResponse),
    {
      onSuccess: (_data, vars) => {
        qc.invalidateQueries(['plugins', 'selfSecrets', vars.pluginName]);
        onSuccess?.('Secret deleted');
      },
      onError: (e) => {
        onError?.(`Failed to delete secret: ${formatError(e)}`);
      },
    }
  );

  if (pluginsWithSecrets.length === 0) {
    return null;
  }

  return (
    <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mr: 1 }}>Plugin Secrets</Typography>
        <HelpTooltip
          title="Some plugins require API keys or other secrets to function. Configure your own secrets here. System-wide secrets (if configured by admin) are used as fallback."
          ariaLabel="help about plugin secrets"
        />
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Configure secrets for plugins that require API keys or credentials.
      </Typography>
      <Stack spacing={1}>
        {pluginsWithSecrets.map((pl) => {
          const isExpanded = expandedPlugin === pl.name;
          const secretsQ = secretsQueries[pl.name];
          const existingKeys = secretsQ?.data?.keys || [];
          const inputs = secretInputs[pl.name] || {};
          return (
            <Box key={pl.name} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, p: 1 }}>
              <Stack direction="row" alignItems="center" justifyContent="space-between" onClick={() => setExpandedPlugin(isExpanded ? null : pl.name)} sx={{ cursor: 'pointer' }}>
                <Box>
                  <Typography variant="subtitle2">{pl.label}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Required: {pl.requiredKeys.join(', ')}
                    {existingKeys.length > 0 && ` | Configured: ${existingKeys.join(', ')}`}
                  </Typography>
                </Box>
                <IconButton size="small">
                  {isExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                </IconButton>
              </Stack>
              <Collapse in={isExpanded}>
                <Divider sx={{ my: 1 }} />
                <Stack spacing={1.5}>
                  {pl.requiredKeys.map((key) => {
                    const isConfigured = existingKeys.includes(key);
                    const inputVal = inputs[key] || '';
                    return (
                      <Box key={key}>
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <TextField
                            label={key}
                            type="password"
                            size="small"
                            fullWidth
                            value={inputVal}
                            onChange={(e) => setSecretInputs((prev) => ({
                              ...prev,
                              [pl.name]: { ...(prev[pl.name] || {}), [key]: e.target.value }
                            }))}
                            placeholder={isConfigured ? '(configured - enter new value to update)' : 'Enter secret value'}
                            InputProps={{
                              sx: isConfigured ? { backgroundColor: 'action.hover' } : {},
                            }}
                          />
                          <Button
                            variant="contained"
                            size="small"
                            disabled={!inputVal || setSecretMut.isLoading}
                            onClick={() => setSecretMut.mutate({ pluginName: pl.name, key, value: inputVal })}
                          >
                            Save
                          </Button>
                          {isConfigured && (
                            <Tooltip title="Delete this secret">
                              <IconButton
                                size="small"
                                color="error"
                                disabled={deleteSecretMut.isLoading}
                                onClick={() => deleteSecretMut.mutate({ pluginName: pl.name, key })}
                              >
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          )}
                        </Stack>
                      </Box>
                    );
                  })}
                </Stack>
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </Paper>
  );
}

