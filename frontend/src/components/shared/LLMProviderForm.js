import React from 'react';
import {
  Grid,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Switch,
  FormControlLabel,
  Box,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Alert,
  Link,
} from '@mui/material';
import { ExpandMore as ExpandMoreIcon, HelpOutline } from '@mui/icons-material';
import SecureTextField from '../SecureTextField';
import NotImplemented from '../NotImplemented';
import HelpTooltip from '../HelpTooltip';
import { getProviderSetupInstructions } from '../../utils/providerSetupGuide';

/**
 * Shared form for Create/Edit LLM Provider dialogs.
 * Props:
 * - provider: object (value)
 * - onProviderChange: (next) => void
 * - providerTypes: array of { key, display_name, is_active }
 * - onProviderTypeChange: (key) => void
 * - baseEndpoints: object map from provider type definition (or {})
 * - endpointsOverride: object map of overrides (or {})
 * - onUpdateEndpointField: (epKey, field, value) => void
 */

const LLMProviderForm = ({
  provider,
  onProviderChange,
  providerTypes,
  onProviderTypeChange,
  baseEndpoints,
  providerCapabilities = {},
  endpointsOverride,
  onUpdateEndpointField,
}) => {
  const base = baseEndpoints || {};
  const caps = providerCapabilities || {};
  const effectiveCaps = (() => {
    const fromProvider =
      provider.provider_capabilities && Object.keys(provider.provider_capabilities).length > 0
        ? provider.provider_capabilities
        : null;
    if (fromProvider) {
      return fromProvider;
    }

    const fromCaps = Object.keys(caps).length > 0 ? caps : null;
    if (fromCaps) {
      return fromCaps;
    }

    return {};
  })();

  const getEffectiveEndpoint = (key) => {
    const b = base[key] || {};
    const o = (endpointsOverride || {})[key] || {};
    return {
      ...(typeof b === 'object' ? b : {}),
      ...(typeof o === 'object' ? o : {}),
    };
  };

  // Get setup instructions for current provider type
  const setupInstructions = getProviderSetupInstructions(provider.provider_type);

  return (
    <Grid container spacing={2}>
      {/* Setup Instructions Alert */}
      {setupInstructions && (
        <Grid item xs={12}>
          <Alert severity="info" icon={<HelpOutline />}>
            <Typography variant="subtitle2" sx={{ fontWeight: 'bold', mb: 1 }}>
              {setupInstructions.title}
            </Typography>
            <Typography variant="body2" component="div">
              <ol style={{ margin: 0, paddingLeft: 20 }}>
                {setupInstructions.steps.map((step, index) => (
                  <li key={index}>{step}</li>
                ))}
              </ol>
            </Typography>
            {setupInstructions.apiKeyUrl && (
              <Typography variant="body2" sx={{ mt: 1 }}>
                <strong>API Key Location:</strong>{' '}
                <Link href={setupInstructions.apiKeyUrl} target="_blank" rel="noopener noreferrer">
                  {setupInstructions.apiKeyUrl}
                </Link>
              </Typography>
            )}
          </Alert>
        </Grid>
      )}

      <Grid item xs={12} sm={6}>
        <TextField
          fullWidth
          label="Provider Name"
          value={provider.name}
          onChange={(e) => onProviderChange({ ...provider, name: e.target.value })}
          margin="normal"
        />
      </Grid>
      <Grid item xs={12} sm={6}>
        <FormControl fullWidth margin="normal">
          <InputLabel>Provider Type</InputLabel>
          <Select
            value={provider.provider_type}
            label="Provider Type"
            onChange={(e) => onProviderTypeChange(e.target.value)}
          >
            {(providerTypes || []).map((pt) => (
              <MenuItem key={pt.key} value={pt.key} disabled={!pt.is_active}>
                {pt.display_name || pt.key}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Grid>

      <Grid item xs={12}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1 }}>
          <Box sx={{ flex: 1 }}>
            <SecureTextField
              label="API Key"
              value={provider.api_key || ''}
              onChange={(e) => onProviderChange({ ...provider, api_key: e.target.value })}
              hasExistingValue={!!provider.has_api_key}
              placeholder={provider.has_api_key ? 'Leave empty to keep existing key' : undefined}
              editPlaceholder="Enter API key"
              helperText={setupInstructions ? `Format: ${setupInstructions.apiKeyFormat}` : undefined}
            />
          </Box>
          <Box sx={{ mt: 3 }}>
            <HelpTooltip
              title={
                <Box>
                  <Typography variant="body2" sx={{ mb: 1 }}>
                    Enter your API key for this provider. Keep this secure and never share it.
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 'bold', mb: 0.5 }}>
                    Where to find your API key:
                  </Typography>
                  <Typography variant="body2" component="div">
                    • <strong>OpenAI:</strong> platform.openai.com/api-keys
                    <br />• <strong>Anthropic:</strong> console.anthropic.com/settings/keys
                    <br />• <strong>Ollama:</strong> No API key needed (local)
                    <br />• <strong>LM Studio:</strong> No API key needed (local)
                    <br />• <strong>Azure OpenAI:</strong> Azure Portal → Your resource → Keys and Endpoint
                  </Typography>
                  {setupInstructions && (
                    <>
                      <Typography variant="body2" sx={{ fontWeight: 'bold', mt: 1, mb: 0.5 }}>
                        Expected format:
                      </Typography>
                      <Typography variant="body2">{setupInstructions.apiKeyFormat}</Typography>
                    </>
                  )}
                </Box>
              }
              ariaLabel="API key help"
            />
          </Box>
        </Box>
      </Grid>

      <Grid item xs={12} sm={6}>
        <TextField
          fullWidth
          label="Organization ID (Optional)"
          value={provider.organization_id || ''}
          onChange={(e) => onProviderChange({ ...provider, organization_id: e.target.value })}
          margin="normal"
        />
      </Grid>
      <Grid item xs={12} sm={6}>
        <TextField
          fullWidth
          label="Monthly Budget Limit ($)"
          type="number"
          value={provider.budget_limit_monthly ?? ''}
          onChange={(e) =>
            onProviderChange({
              ...provider,
              budget_limit_monthly: e.target.value ? parseFloat(e.target.value) : null,
            })
          }
          margin="normal"
        />
        <Box sx={{ mt: 0.5 }}>
          <NotImplemented label="Budget cap not enforced server-side yet" />
        </Box>
      </Grid>

      <Grid item xs={12} sm={6}>
        <Box sx={{ mt: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <TextField
              fullWidth
              label="Rate Limit (RPM)"
              type="number"
              value={provider.rate_limit_rpm}
              onChange={(e) =>
                onProviderChange({
                  ...provider,
                  rate_limit_rpm: parseInt(e.target.value || '0', 10),
                })
              }
              margin="normal"
              helperText="Requests per minute limit for this provider"
            />
            <HelpTooltip title="Set to 0 to disable rate limiting for this provider" />
          </Box>
        </Box>
      </Grid>

      <Grid item xs={12} sm={6}>
        <Box sx={{ mt: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <TextField
              fullWidth
              label="Token Rate Limit (TPM)"
              type="number"
              value={provider.rate_limit_tpm}
              onChange={(e) =>
                onProviderChange({
                  ...provider,
                  rate_limit_tpm: parseInt(e.target.value || '0', 10),
                })
              }
              margin="normal"
              helperText="Tokens per minute limit for this provider"
            />
            <HelpTooltip title="Set to 0 to disable token rate limiting for this provider" />
          </Box>
        </Box>
      </Grid>

      {/* Endpoints configuration - Advanced options accordion */}
      {base && Object.keys(base).length > 0 && (
        <Grid item xs={12}>
          <Accordion>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography variant="subtitle1">Advanced options</Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Box sx={{ p: 1 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                  <Typography variant="h6">Basic Provider Settings</Typography>
                  <HelpTooltip
                    title="Defaults come from Provider Type Definition. Overrides here take precedence and are stored on the provider."
                    ariaLabel="Basic provider settings help"
                  />
                </Box>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Change the URL at which a provider can be called.
                </Typography>

                <Grid item xs={12}>
                  <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1 }}>
                    <TextField
                      fullWidth
                      label="API Endpoint"
                      value={provider.api_endpoint}
                      onChange={(e) => onProviderChange({ ...provider, api_endpoint: e.target.value })}
                      margin="normal"
                      helperText={
                        setupInstructions
                          ? `Default: ${setupInstructions.defaultEndpoint}`
                          : 'Base URL for API requests'
                      }
                    />
                    <Box sx={{ mt: 3 }}>
                      <HelpTooltip
                        title={
                          <Box>
                            <Typography variant="body2" sx={{ mb: 1 }}>
                              The base URL where API requests will be sent.
                            </Typography>
                            <Typography variant="body2" sx={{ fontWeight: 'bold', mb: 0.5 }}>
                              Common endpoints:
                            </Typography>
                            <Typography variant="body2" component="div">
                              • <strong>OpenAI:</strong> https://api.openai.com/v1
                              <br />• <strong>Anthropic:</strong> https://api.anthropic.com
                              <br />• <strong>Ollama:</strong> http://localhost:11434
                              <br />• <strong>LM Studio:</strong> http://localhost:1234/v1
                              <br />• <strong>Azure:</strong> https://your-resource.openai.azure.com
                            </Typography>
                          </Box>
                        }
                        ariaLabel="API endpoint help"
                      />
                    </Box>
                  </Box>
                </Grid>
              </Box>

              <Box sx={{ p: 1 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                  <Typography variant="h6">Endpoints</Typography>
                  <HelpTooltip
                    title="Defaults come from Provider Type Definition. Overrides here take precedence and are stored on the provider."
                    ariaLabel="Endpoints help"
                  />
                </Box>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  These fields change the way the provider is called. While most providers follow a standard format,
                  some may require customizing these fields. We accept comma separated lists of JMESPath formats.
                </Typography>

                {Object.keys(base).map((epKey) => {
                  const eff = getEffectiveEndpoint(epKey);
                  const b = base[epKey] || {};
                  const cur = (endpointsOverride || {})[epKey] || {};
                  return (
                    <Box
                      key={epKey}
                      sx={{
                        mb: 2,
                        p: 1.5,
                        border: '1px dashed',
                        borderColor: 'divider',
                        borderRadius: 1,
                      }}
                    >
                      <Typography variant="subtitle1">{eff.label || `API - ${epKey}`}</Typography>
                      <Grid container>
                        <Grid item xs={12}>
                          <TextField
                            fullWidth
                            label="Path"
                            value={cur.path !== undefined ? cur.path : b.path || ''}
                            onChange={(e) => onUpdateEndpointField(epKey, 'path', e.target.value)}
                            helperText="Relative path appended to provider base URL"
                            margin="dense"
                          />
                        </Grid>
                        {/* Dynamic options from definition/override */}
                        {(() => {
                          const bOpts = b.options && typeof b.options === 'object' ? b.options : {};
                          const cOpts = cur.options && typeof cur.options === 'object' ? cur.options : {};
                          const keys = Array.from(new Set([...Object.keys(bOpts), ...Object.keys(cOpts)])).sort();
                          if (keys.length === 0) {
                            return null;
                          }
                          return (
                            <>
                              <Grid item xs={12}>
                                <Typography variant="subtitle2" sx={{ mt: 1 }}>
                                  Options
                                </Typography>
                              </Grid>
                              {keys.map((optKey) => {
                                const baseOpt = bOpts[optKey] || {};
                                const curOpt = cOpts[optKey] || {};
                                const optLabel = curOpt.label || baseOpt.label || optKey;
                                const optVal =
                                  curOpt.value !== undefined
                                    ? curOpt.value
                                    : baseOpt.value !== undefined
                                      ? baseOpt.value
                                      : '';
                                return (
                                  <Grid item xs={12} md={6} key={optKey}>
                                    <TextField
                                      fullWidth
                                      label={optLabel}
                                      value={optVal}
                                      onChange={(e) => {
                                        const value = e.target.value;
                                        const nextOpt = {
                                          ...curOpt,
                                          value,
                                          label: optLabel,
                                        };
                                        const nextOpts = {
                                          ...cOpts,
                                          [optKey]: nextOpt,
                                        };
                                        onUpdateEndpointField(epKey, 'options', nextOpts);
                                      }}
                                      margin="dense"
                                    />
                                  </Grid>
                                );
                              })}
                            </>
                          );
                        })()}
                      </Grid>
                    </Box>
                  );
                })}
              </Box>
            </AccordionDetails>
          </Accordion>
        </Grid>
      )}

      <Grid item xs={12}>
        <FormControlLabel
          control={
            <Switch
              checked={!!provider.is_active}
              onChange={(e) => onProviderChange({ ...provider, is_active: e.target.checked })}
            />
          }
          label="Active"
        />
        {Object.entries(effectiveCaps || {}).map(([capKey, capVal]) => (
          <FormControlLabel
            key={capKey}
            control={
              <Switch
                checked={!!capVal?.value}
                onChange={(e) => {
                  const nextCaps = {
                    ...effectiveCaps,
                    [capKey]: {
                      label: capVal?.label || capKey,
                      value: e.target.checked,
                    },
                  };
                  onProviderChange({
                    ...provider,
                    provider_capabilities: nextCaps,
                  });
                }}
              />
            }
            label={capVal?.label || capKey}
          />
        ))}
      </Grid>
    </Grid>
  );
};

export default LLMProviderForm;
