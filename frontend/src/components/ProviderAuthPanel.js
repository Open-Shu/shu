import React, { useEffect, useMemo, useState } from 'react';
import { Box, Button, Alert, TextField, Typography, FormControl, InputLabel, Select, MenuItem, Tooltip, CircularProgress } from '@mui/material';
import IdentityGate from './IdentityGate';
import { hostAuthAPI, extractDataFromResponse, formatError } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import useOAuthAuthorize from '../hooks/useOAuthAuthorize';



/*
  ProviderAuthPanel — DRY, provider-agnostic OAuth config/readiness UI
  Props:
    - plugin: plugin descriptor (should include required_identities and op_auth)
    - op: current operation name (string) if applicable
    - onGateChange?: function(ok: boolean) — reports whether identity gate allows action

  Behavior:
    - If op_auth[op].mode === 'user': show IdentityGate for the provider in that spec
    - If op_auth[op].mode !== 'user' (e.g., service account / delegation): show a readiness probe UI
    - Fallback: if op_auth is missing, but required_identities exists, show IdentityGate for those providers
*/
export default function ProviderAuthPanel({ plugin, op = '', onGateChange = null, onAuthOverlayChange = null, initialOverlay = null, pluginName = null }) {
  const { user } = useAuth();
  const opKey = String(op || '').toLowerCase();
  const pluginDef = plugin;
  const opAuthSpec = useMemo(() => {
    try { return pluginDef?.op_auth && pluginDef.op_auth[opKey]; } catch (_) { return null; }
  }, [pluginDef, opKey]);
  const provider = opAuthSpec?.provider || (Array.isArray(pluginDef?.required_identities) && pluginDef.required_identities[0]?.provider) || null;
  const mode = opAuthSpec?.mode || (Array.isArray(pluginDef?.required_identities) && pluginDef.required_identities[0]?.mode) || null;
  const scopes = opAuthSpec?.scopes || (Array.isArray(pluginDef?.required_identities) && pluginDef.required_identities[0]?.scopes) || [];


  const [googleStatus, setGoogleStatus] = useState(null);
  useEffect(() => {
    let mounted = true;
    if (provider === 'google') {
      hostAuthAPI.status('google')
        .then(extractDataFromResponse)
        .then((data) => { if (mounted) setGoogleStatus((data && data.google) || {}); })
        .catch(() => { if (mounted) setGoogleStatus({}); });
    } else {
      setGoogleStatus(null);
    }
    return () => { mounted = false; };
  }, [provider]);

  const [selectedMode, setSelectedMode] = useState(mode || null);
  useEffect(() => {
    setSelectedMode(mode || null);
    // Reset probe state when default mode changes
    setProbeResult(null);
    setProbeError(null);
  }, [mode]);

  // Hydrate selection/subject from persisted overlay when editing
  useEffect(() => {
    try {
      const a = initialOverlay && initialOverlay.auth ? initialOverlay.auth : null;
      if (!a || !provider) return;
      const prov = a[provider];
      if (!prov) return;
      if (typeof prov.mode === 'string' && prov.mode) setSelectedMode(prov.mode);
      if (typeof prov.subject === 'string' && prov.subject) setProbeSubject(prov.subject);
      if (typeof prov.impersonate_email === 'string' && prov.impersonate_email) setProbeSubject(prov.impersonate_email);
    } catch (_) {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialOverlay, provider]);

  // Reset probe status when selection changes
  useEffect(() => {
    setProbeResult(null);
    setProbeError(null);
  }, [selectedMode]);

  // Identity gate
  // Important: when an op_auth spec exists with explicit scopes for this op, request those scopes for the user OAuth connect flow.
  // Otherwise fall back to manifest.required_identities filtered by provider.
  const requiredIdentities = useMemo(() => {
    if (!provider) return Array.isArray(pluginDef?.required_identities) ? pluginDef.required_identities : [];
    // Prioritize op-specific scopes if available
    const opScopes = Array.isArray(opAuthSpec?.scopes) ? opAuthSpec.scopes : [];
    if (opScopes.length > 0) {
      return [{ provider, scopes: opScopes, mode: 'user' }];
    }
    const all = Array.isArray(pluginDef?.required_identities) ? pluginDef.required_identities : [];
    return all.filter((ri) => ri?.provider === provider);
  }, [pluginDef, provider, opAuthSpec]);
  const [identitiesOk, setIdentitiesOk] = useState(true);


  // Probe (service account / delegation)
  const [probeSubject, setProbeSubject] = useState(user?.email || '');
  // Allowed modes from manifest (op_auth.allowed_modes). If absent, treat opAuthSpec.mode as the only allowed.
  const allowedModes = useMemo(() => {
    const raw = Array.isArray(opAuthSpec?.allowed_modes) ? opAuthSpec.allowed_modes : null;
    if (raw && raw.length > 0) return raw.map((m) => String(m).toLowerCase());
    const m = String(opAuthSpec?.mode || '').toLowerCase();
    return m ? [m] : [];
  }, [opAuthSpec]);
  // Build available auth modes for provider based on host config intersected with allowedModes
  const availableModes = useMemo(() => {
    const modes = [];
    const saConfigured = !!(googleStatus && googleStatus.service_account_configured);
    const userOauthConfigured = !!(googleStatus && googleStatus.user_oauth_configured);
    if (provider === 'google') {
      const candidates = [];
      if (userOauthConfigured) candidates.push('user');
      if (saConfigured) { candidates.push('domain_delegate'); candidates.push('service_account'); }
      const final = (allowedModes && allowedModes.length > 0) ? candidates.filter((v) => allowedModes.includes(v)) : candidates;
      final.forEach((v) => {
        const label = v === 'user' ? 'User OAuth' : (v === 'domain_delegate' ? 'Domain-wide Delegation (impersonation)' : 'Domain-wide Delegation (service account)');
        modes.push({ value: v, label });
      });
    }
    return modes;
  }, [googleStatus, provider, allowedModes]);
  // Ensure selection is one of the available modes
  useEffect(() => {
    const vals = availableModes.map((m) => m.value);
    const current = String(selectedMode || mode || '').toLowerCase();
    if (vals.length > 0 && !vals.includes(current)) {
      setSelectedMode(vals[0]);
    }
  }, [availableModes, selectedMode, mode]);
  const effMode = String(selectedMode || mode || '').toLowerCase();

  // Compute gating: selected mode must be satisfied exactly; no fallback
  useEffect(() => {
    try {
      if (typeof onGateChange !== 'function') return;
      const m = String(effMode || '').toLowerCase();
      let ok = true;
      if (m === 'user') {
        ok = !!identitiesOk;
      } else if (m === 'domain_delegate') {
        ok = !!String(probeSubject || '').trim();
      } else if (m === 'service_account') {
        ok = !!(googleStatus && googleStatus.service_account_configured);
      }
      onGateChange(ok);
    } catch (_) {}
  }, [effMode, identitiesOk, probeSubject, googleStatus, onGateChange]);


  const [probeLoading, setProbeLoading] = useState(false);
  const [probeResult, setProbeResult] = useState(null);
  const [probeError, setProbeError] = useState(null);

  const runProbe = async () => {
    if (!provider || !Array.isArray(scopes) || scopes.length === 0) return;
    try {
      setProbeLoading(true);
      setProbeError(null);
      setProbeResult(null);
      const m = String(effMode);
      let resp;
      if (m === 'domain_delegate') {
        const subject = String(probeSubject || '').trim();
        if (!subject) { setProbeError('Enter an impersonation email first.'); setProbeLoading(false); return; }
        resp = await hostAuthAPI.delegationCheck(provider, subject, scopes);
      } else if (m === 'service_account') {
        resp = await hostAuthAPI.serviceAccountCheck(provider, scopes);
      } else {
        setProbeLoading(false);
        return;
      }
      const data = extractDataFromResponse(resp);
      setProbeResult(data);
    } catch (e) {
      setProbeError(formatError(e));
    } finally {
      setProbeLoading(false);
    }
  };


  // Emit selected auth overlay to parent so execution/feed can honor it
  useEffect(() => {
    try {
      if (typeof onAuthOverlayChange !== 'function') return;
      const overlay = {};
      if (provider === 'google') {
        const payload = { mode: effMode };
        if (effMode === 'domain_delegate') {
          const subj = String(probeSubject || '').trim();
          if (subj) payload.subject = subj;
        }
        overlay.auth = { google: payload };
      }
      onAuthOverlayChange(overlay);
    } catch (_) {}
  }, [provider, effMode, probeSubject, onAuthOverlayChange]);

  const showIdentityGate = (effMode === 'user') && requiredIdentities.length > 0;
  const showProbe = (effMode === 'domain_delegate' && provider === 'google');
  const showSaProbe = (effMode === 'service_account' && provider === 'google');

  return (
    <Box>
      <Typography variant="subtitle1" gutterBottom>Authentication</Typography>

      {provider === 'google' && availableModes.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <FormControl size="small" sx={{ minWidth: 260 }}>
            <InputLabel id="auth-mode-label">Auth Mode</InputLabel>
            <Select
              labelId="auth-mode-label"
              label="Auth Mode"
              value={effMode || ''}
              onChange={(e) => setSelectedMode(e.target.value)}
            >
              {availableModes.map((opt) => (
                <MenuItem key={opt.value} value={opt.value}>{opt.label}</MenuItem>
              ))}
            </Select>
          </FormControl>
          {effMode !== 'user' && !(googleStatus && googleStatus.service_account_configured) && (
            <Alert severity="warning" sx={{ mt: 1 }}>
              Service account is not configured on the host. Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE.
            </Alert>
          )}
        </Box>
      )}
      {/* Subscription controls for this plugin (authorize/unauthorize) */}
      {provider && pluginName && effMode === 'user' && (
        <SubscriptionControls
          provider={provider}
          pluginName={pluginName}
          requiredScopes={Array.isArray(opAuthSpec?.scopes) ? opAuthSpec.scopes : (requiredIdentities[0]?.scopes || [])}
        />
      )}


      {showIdentityGate && (
        <>
          <IdentityGate
            title={null}
            requiredIdentities={requiredIdentities}
            onStatusChange={(ok) => setIdentitiesOk(!!ok)}
            identityStatusProps={{ useServerUnionForAuthorize: true, hideConnectButton: true, showDisconnect: false }}
          />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Scopes requested for this operation:
          </Typography>
          <Box sx={{ ml: 2 }}>
            {(Array.isArray(opAuthSpec?.scopes) ? opAuthSpec.scopes : (requiredIdentities[0]?.scopes || [])).map((s) => (
              <Typography key={s} variant="caption" display="block">{s}</Typography>
            ))}
          </Box>
        </>
      )}

      {showProbe && (
        <Box sx={{ mb: 2 }}>
          <Box display="flex" alignItems="center" gap={2}>
            <TextField
              size="small"
              label="Impersonation Email"
              value={probeSubject}
              onChange={(e) => setProbeSubject(e.target.value)}
              placeholder="user@example.com"
              sx={{ minWidth: 360 }}
            />
            <Button variant="outlined" onClick={runProbe} disabled={probeLoading}>
              {probeLoading ? 'Testing…' : 'Test Auth'}
            </Button>
            {probeResult ? (probeResult.ready ? (
              <Alert severity="success" sx={{ m: 0 }}>
                Authorized (status {probeResult.status}).
              </Alert>
            ) : (
              <Alert severity="warning" sx={{ m: 0 }}>
                Not authorized (status {probeResult.status}). {probeResult?.error?.message || 'See server logs for details.'}
              </Alert>
            )) : (probeError && (
              <Alert severity="error" sx={{ m: 0 }}>{probeError}</Alert>
            ))}
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Use Test Auth to verify your service account is authorized to impersonate the selected user for the required scopes.
          </Typography>
        </Box>
      )}

      {showSaProbe && (
        <Box sx={{ mb: 2 }}>
          <Box display="flex" alignItems="center" gap={2}>
            <Button variant="outlined" onClick={runProbe} disabled={probeLoading}>
              {probeLoading ? 'Testing…' : 'Test Auth'}
            </Button>
            {probeResult ? (probeResult.ready ? (
              <Alert severity="success" sx={{ m: 0 }}>
                Authorized (status {probeResult.status}).
              </Alert>
            ) : (
              <Alert severity="warning" sx={{ m: 0 }}>
                Not authorized (status {probeResult.status}). {probeResult?.error?.message || 'See server logs for details.'}
              </Alert>
            )) : (probeError && (
              <Alert severity="error" sx={{ m: 0 }}>{probeError}</Alert>


            ))}
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Use Test Auth to verify your service account can obtain an access token for the required scopes.
          </Typography>
        </Box>
      )}
    </Box>
  );
}

export const authGateDisabled = (plugin, op, identitiesOk) => {
  try {
    const spec = plugin?.op_auth && plugin.op_auth[String(op || '').toLowerCase()];
    const mode = spec?.mode || null;
    const req = Array.isArray(plugin?.required_identities) ? plugin.required_identities : [];

    if (mode === 'user' && req.length > 0) return !identitiesOk;
    return false;
  } catch (_) {
    return false;
  }
};



// Local component: subscription controls that align plugin form with Connected Accounts flows
function SubscriptionControls({ provider, pluginName, requiredScopes = [] }) {
  const qc = useQueryClient();
  const [authorizing, setAuthorizing] = useState(false);

  // Current subscription state for this provider
  const subsQ = useQuery(['hostAuth','subscriptions',provider], () => hostAuthAPI.listSubscriptions(provider).then(extractDataFromResponse), { enabled: !!provider });
  const items = Array.isArray(subsQ?.data?.items) ? subsQ.data.items : [];
  const isSubscribed = items.some((s) => s.plugin_name === pluginName);

  const subscribeMut = useMutation(() => hostAuthAPI.subscribe(provider, pluginName).then(extractDataFromResponse), {
    onSuccess: () => {
      qc.invalidateQueries(['hostAuth','subscriptions',provider]);
      qc.invalidateQueries(['hostAuth','consentScopes',provider]);
    },
  });
  const unsubscribeMut = useMutation(() => hostAuthAPI.unsubscribe(provider, pluginName).then(extractDataFromResponse), {
    onSuccess: () => {
      qc.invalidateQueries(['hostAuth','subscriptions',provider]);
      qc.invalidateQueries(['hostAuth','consentScopes',provider]);
    },
  });

  const { startAuthorize } = useOAuthAuthorize();
  const handleAuthorize = async () => {
    await startAuthorize({
      provider,
      scopes: Array.isArray(requiredScopes) ? requiredScopes : [],
      onStart: () => setAuthorizing(true),
      onDone: () => setAuthorizing(false),
      onAfterExchange: async () => {
        if (!isSubscribed) {
          try { await subscribeMut.mutateAsync(); } catch (_) {}
        }
        try {
          qc.invalidateQueries(['hostAuth','status']);
          qc.invalidateQueries(['hostAuth','subscriptions',provider]);
          qc.invalidateQueries(['hostAuth','consentScopes',provider]);
        } catch (_) {}
      },
      onError: (e) => { /* eslint-disable-next-line no-console */ console.error('Authorize failed:', formatError(e)); },
    });
  };

  return (
    <Box sx={{ mb: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
      <Typography variant="body2" color="text.secondary">
        {isSubscribed ? `Subscribed to ${provider} for this plugin` : `Not subscribed to ${provider} for this plugin`}
      </Typography>
      {authorizing ? (
        <Tooltip title="Authorizing… please complete the OAuth dialog.">
          <span>
            <Button size="small" variant="outlined" disabled>
              <CircularProgress size={14} />
            </Button>
          </span>
        </Tooltip>
      ) : isSubscribed ? (
        <Tooltip title="Unsubscribe this plugin from using this provider. Tokens are not removed; other subscribed plugins remain authorized.">
          <span>
            <Button size="small" variant="outlined" color="warning" disabled={unsubscribeMut.isLoading} onClick={() => unsubscribeMut.mutate()}>
              {unsubscribeMut.isLoading ? <CircularProgress size={14} /> : 'Unauthorize'}
            </Button>
          </span>
        </Tooltip>
      ) : (
        <Tooltip title="Authorize with the minimal scopes required by this plugin. Subscription will be created after successful authorization.">
          <span>
            <Button size="small" variant="outlined" disabled={subscribeMut.isLoading} onClick={handleAuthorize}>
              {subscribeMut.isLoading ? <CircularProgress size={14} /> : 'Authorize'}
            </Button>
          </span>
        </Tooltip>
      )}
    </Box>
  );
}
