import React, { useMemo, useState } from 'react';
import { Box, Typography, Paper, Divider, Checkbox, FormControlLabel, Stack, Tooltip, Button, Snackbar, Alert } from '@mui/material';
import { useQuery, useMutation, useQueryClient, useQueries } from 'react-query';
import api, { extractDataFromResponse, hostAuthAPI, formatError } from '../services/api';
import HelpTooltip from './HelpTooltip.jsx';
import IdentityStatus from './IdentityStatus';
import PluginSecretsSection from './PluginSecretsSection';

export default function ConnectedAccountsPage() {
  // Load all plugins to compute a provider-wide superset of requested scopes (union across plugins)
  const pluginsQ = useQuery(['plugins','list'], () => api.get('/plugins').then(extractDataFromResponse));

  const { requiredIdentities, scopeMapByProvider } = useMemo(() => {
    const plugins = Array.isArray(pluginsQ.data) ? pluginsQ.data : [];
    const byProv = {};
    const ensureProv = (prov) => {
      if (!byProv[prov]) byProv[prov] = { scopes: new Set(), scopeToPlugins: {} };
      return byProv[prov];
    };
    for (const p of plugins) {
      const pluginLabel = p?.display_name || p?.name || 'plugin';
      // Collect scopes from op_auth entries per provider
      const opAuth = p?.op_auth || {};
      if (opAuth && typeof opAuth === 'object') {
        for (const key of Object.keys(opAuth)) {
          const spec = opAuth[key];
          const prov = spec?.provider;
          if (prov && Array.isArray(spec?.scopes)) {
            const bucket = ensureProv(prov);
            spec.scopes.forEach((s) => {
              if (!s) return;
              const scope = String(s);
              bucket.scopes.add(scope);
              bucket.scopeToPlugins[scope] = Array.from(new Set([...(bucket.scopeToPlugins[scope] || []), pluginLabel]));
            });
          }
        }
      }
      // Also include required_identities scopes per provider
      const reqIds = Array.isArray(p?.required_identities) ? p.required_identities : [];
      for (const ri of reqIds) {
        const prov = ri?.provider;
        if (prov && Array.isArray(ri?.scopes)) {
          const bucket = ensureProv(prov);
          ri.scopes.forEach((s) => {
            if (!s) return;
            const scope = String(s);
            bucket.scopes.add(scope);
            bucket.scopeToPlugins[scope] = Array.from(new Set([...(bucket.scopeToPlugins[scope] || []), pluginLabel]));
          });
        }
      }
    }
    // Provider-specific safety adjustments (keep minimal; avoid hardcoding outside prov bucket)
    if (byProv['google']) {
      const unionScopes = Array.from(byProv['google'].scopes);
      const hasAnyGmail = unionScopes.some((s) => s.includes('https://www.googleapis.com/auth/gmail.'));
      const hasGmailReadonly = unionScopes.includes('https://www.googleapis.com/auth/gmail.readonly');
      const hasGmailModify = unionScopes.includes('https://www.googleapis.com/auth/gmail.modify');
      if (hasAnyGmail && !hasGmailReadonly && !hasGmailModify) {
        byProv['google'].scopes.add('https://www.googleapis.com/auth/gmail.readonly');
      }
    }
    const requiredIdentities = Object.entries(byProv).map(([prov, data]) => ({ provider: prov, scopes: Array.from(data.scopes) }));
    const scopeMapByProvider = Object.fromEntries(Object.entries(byProv).map(([prov, data]) => [prov, data.scopeToPlugins]));
    return { requiredIdentities, scopeMapByProvider };
  }, [pluginsQ.data]);

  const qc = useQueryClient();
  const [snack, setSnack] = useState({ open: false, message: '', severity: 'error' });
  const [authorizing, setAuthorizing] = useState({}); // per-provider popup state

  // Helpers: plugins by provider and provider list from requiredIdentities
  const providers = useMemo(() => Object.keys(scopeMapByProvider || {}), [scopeMapByProvider]);
  const pluginsByProvider = useMemo(() => {
    const res = {};
    const plugins = Array.isArray(pluginsQ.data) ? pluginsQ.data : [];
    for (const p of plugins) {
      const label = p?.display_name || p?.name || 'plugin';
      const opAuth = p?.op_auth || {};
      const reqIds = Array.isArray(p?.required_identities) ? p.required_identities : [];
      const provs = new Set();
      // scan op_auth
      if (opAuth && typeof opAuth === 'object') {
        for (const key of Object.keys(opAuth)) {
          const spec = opAuth[key];
          if (spec?.provider) provs.add(String(spec.provider));
        }
      }
      // scan required_identities
      for (const ri of reqIds) {
        if (ri?.provider) provs.add(String(ri.provider));
      }
      for (const prov of provs) {

        res[prov] = res[prov] || [];
        res[prov].push({ name: p?.name, label });
      }
    }
    // sort labels
    for (const k of Object.keys(res)) res[k].sort((a,b)=>a.label.localeCompare(b.label));
    return res;
  }, [pluginsQ.data]);

  // Queries: subscriptions and consent-scopes per provider
  // Queries must be declared at top-level using useQueries (React Hooks rules)
  const subsQueriesArr = useQueries((providers || []).map((prov) => ({
    queryKey: ['hostAuth','subscriptions',prov],
    queryFn: () => hostAuthAPI.listSubscriptions(prov).then(extractDataFromResponse),
    enabled: !!prov,
  })));
  const subsQueries = useMemo(() => Object.fromEntries((providers || []).map((prov, idx) => [prov, subsQueriesArr[idx]])), [providers, subsQueriesArr]);

  const consentQueriesArr = useQueries((providers || []).map((prov) => ({
    queryKey: ['hostAuth','consentScopes',prov],
    queryFn: () => hostAuthAPI.consentScopes(prov).then(extractDataFromResponse),
    enabled: !!prov,
    staleTime: 5000,
  })));
  const consentQueries = useMemo(() => Object.fromEntries((providers || []).map((prov, idx) => [prov, consentQueriesArr[idx]])), [providers, consentQueriesArr]);

  // Mutations: subscribe/unsubscribe
  const subscribeMut = useMutation(({ provider, plugin }) => hostAuthAPI.subscribe(provider, plugin).then(extractDataFromResponse), {
    onMutate: async (vars) => {
      await qc.cancelQueries(['hostAuth','subscriptions', vars.provider]);
      const key = ['hostAuth','subscriptions', vars.provider];
      const previous = qc.getQueryData(key);
      qc.setQueryData(key, (old) => {
        const items = Array.isArray(old?.items) ? old.items : [];
        const exists = items.some((s) => s.plugin_name === vars.plugin);
        return exists ? old : { items: [...items, { plugin_name: vars.plugin }] };
      });
      return { previous };
    },
    onError: (e, vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(['hostAuth','subscriptions', vars.provider], ctx.previous);
      setSnack({ open: true, message: `Subscribe failed: ${formatError(e)}`, severity: 'error' });
    },
    onSettled: (_data, _err, vars) => {
      qc.invalidateQueries(['hostAuth','subscriptions', vars.provider]);
      qc.invalidateQueries(['hostAuth','consentScopes', vars.provider]);
    },
  });
  const unsubscribeMut = useMutation(({ provider, plugin }) => hostAuthAPI.unsubscribe(provider, plugin).then(extractDataFromResponse), {
    onMutate: async (vars) => {
      await qc.cancelQueries(['hostAuth','subscriptions', vars.provider]);
      const key = ['hostAuth','subscriptions', vars.provider];
      const previous = qc.getQueryData(key);
      qc.setQueryData(key, (old) => {
        const items = Array.isArray(old?.items) ? old.items : [];
        return { items: items.filter((s) => s.plugin_name !== vars.plugin) };
      });
      return { previous };
    },
    onError: (e, vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(['hostAuth','subscriptions', vars.provider], ctx.previous);
      setSnack({ open: true, message: `Unsubscribe failed: ${formatError(e)}`, severity: 'error' });
    },
    onSettled: (_data, _err, vars) => {
      qc.invalidateQueries(['hostAuth','subscriptions', vars.provider]);
      qc.invalidateQueries(['hostAuth','consentScopes', vars.provider]);
    },
  });
  // Compute per-provider per-plugin scopes for tooltips
  const pluginsByProviderScopes = useMemo(() => {
    const res = {};
    const plugins = Array.isArray(pluginsQ.data) ? pluginsQ.data : [];
    for (const p of plugins) {
      const name = p?.name;
      if (!name) continue;
      const opAuth = p?.op_auth || {};
      const reqIds = Array.isArray(p?.required_identities) ? p.required_identities : [];
      const scopesByProv = {};
      if (opAuth && typeof opAuth === 'object') {
        for (const key of Object.keys(opAuth)) {
          const spec = opAuth[key];
          const prov = spec?.provider;
          const sc = Array.isArray(spec?.scopes) ? spec.scopes : [];
          if (prov) scopesByProv[prov] = Array.from(new Set([...(scopesByProv[prov] || []), ...sc.map(String)]));
        }
      }
      for (const ri of reqIds) {
        const prov = ri?.provider;
        const sc = Array.isArray(ri?.scopes) ? ri.scopes : [];
        if (prov) scopesByProv[prov] = Array.from(new Set([...(scopesByProv[prov] || []), ...sc.map(String)]));
      }
      for (const prov of Object.keys(scopesByProv)) {
        if (!res[prov]) res[prov] = {};
        res[prov][name] = scopesByProv[prov];
      }
    }
    return res;
  }, [pluginsQ.data]);

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
        <Typography variant="h5" sx={{ fontWeight: 700, mr: 1 }}>
          Plugin Subscriptions
        </Typography>
        <HelpTooltip
          title="Connect accounts, subscribe to plugins, and configure secrets. We request the union of scopes across subscribed plugins per provider."
          ariaLabel="help about plugin subscriptions"
        />
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Manage provider connections, plugin subscriptions, and secrets. The server requests consent for the union of scopes across your subscribed plugins.
      </Typography>

      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Accounts</Typography>
        </Box>
        <Divider sx={{ my: 1.5 }} />
        {/* Use server union by omitting scopes */}
        <IdentityStatus
          requiredIdentities={requiredIdentities}
          showManageLink={false}
          useServerUnionForAuthorize
          authorizingMap={authorizing}
          setAuthorizingMap={setAuthorizing}
        />
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Plugin Subscriptions</Typography>
        {providers.length === 0 ? (
          <Typography variant="body2" color="text.secondary">No providers detected from installed plugins.</Typography>
        ) : (
          <Box>
            {providers.map((prov) => {
              const subsQ = subsQueries[prov];
              const consentQ = consentQueries[prov];
              const subs = Array.isArray(subsQ?.data?.items) ? subsQ.data.items : [];
              const subscribed = new Set(subs.map(s => s.plugin_name));
              const items = pluginsByProvider[prov] || [];
              return (
                <Box key={prov} sx={{ mb: 2 }}>
                  <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
                    <Typography variant="subtitle2">{prov}</Typography>
                    <Tooltip title="Server-computed union of scopes from subscribed plugins">
                      <Typography variant="caption" color="text.secondary">
                        {consentQ?.data?.scopes?.length ? `${consentQ.data.scopes.length} scopes (server union)` : 'No scopes (subscribe plugins to request scopes)'}
                      </Typography>
                    </Tooltip>
                    <Button size="small" variant="text" onClick={() => qc.invalidateQueries(['hostAuth','consentScopes',prov])}>Refresh</Button>
                  </Stack>
                  {items.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">No installed plugins require {prov}.</Typography>
                  ) : (
                    <Stack>
                      {items.map((pl) => {
                        const checked = subscribed.has(pl.name);
                        const pluginScopes = (pluginsByProviderScopes?.[prov]?.[pl.name]) || [];
                        const labelNode = (
                          <Tooltip title={pluginScopes.length ? pluginScopes.join('\n') : ''}>
                            <span>{pl.label}</span>
                          </Tooltip>
                        );
                        return (
                          <FormControlLabel key={`${prov}:${pl.name}`}
                            control={<Checkbox size="small" checked={checked} onChange={(e) => {
                              const next = e.target.checked;
                              if (next) subscribeMut.mutate({ provider: prov, plugin: pl.name });
                              else unsubscribeMut.mutate({ provider: prov, plugin: pl.name });
                            }} />}
                            label={labelNode}
                          />
                        );
                      })}
                    </Stack>
                  )}
                </Box>
              );
            })}
          </Box>
        )}
      </Paper>

      {/* Plugin Secrets Section */}
      <PluginSecretsSection
        plugins={pluginsQ.data}
        onSuccess={(msg) => setSnack({ open: true, message: msg, severity: 'success' })}
        onError={(msg) => setSnack({ open: true, message: msg, severity: 'error' })}
      />

      <Snackbar open={snack.open} autoHideDuration={4000} onClose={() => setSnack((s)=>({ ...s, open: false }))}>
        <Alert onClose={() => setSnack((s)=>({ ...s, open: false }))} severity={snack.severity} sx={{ width: '100%' }}>
          {snack.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}

