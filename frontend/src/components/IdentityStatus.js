import React, { useEffect, useMemo, useCallback, useState } from "react";
import { useQuery, useQueryClient, useQueries } from "react-query";
import {
  Box,
  Chip,
  Button,
  Stack,
  Tooltip,
  CircularProgress,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import { hostAuthAPI, extractDataFromResponse } from "../services/api";
import useOAuthAuthorize from "../hooks/useOAuthAuthorize";

/**
 * IdentityStatus
 * - props.requiredIdentities: array of { provider: 'google', mode: 'user'|'system', scopes: [..] }
 * - props.onStatusChange: (allConnected: boolean, rawStatus: object) => void
 */

export default function IdentityStatus({
  requiredIdentities = [],
  onStatusChange,
  showManageLink = true,
  useServerUnionForAuthorize = false,
  hideConnectButton = false,
  showDisconnect = true,
  authorizingMap = null,
  setAuthorizingMap = null,
}) {
  const qc = useQueryClient();
  const providers = useMemo(() => {
    const set = new Set(
      requiredIdentities
        .map((r) => (r.provider || "").toLowerCase())
        .filter(Boolean),
    );
    return Array.from(set);
  }, [requiredIdentities]);

  const scopesByProvider = useMemo(() => {
    const map = {};
    for (const r of requiredIdentities) {
      const p = (r.provider || "").toLowerCase();
      if (!p) {
        continue;
      }
      const scopes = Array.isArray(r.scopes) ? r.scopes : [];
      map[p] = Array.from(new Set([...(map[p] || []), ...scopes]));
    }
    return map;
  }, [requiredIdentities]);

  const providersCsv = providers.join(",");
  // Allow parent to control authorizing state so multiple buttons disable consistently during OAuth popup
  const [internalAuthorizing, setInternalAuthorizing] = useState({});
  const authorizing = authorizingMap || internalAuthorizing;
  const setAuthorizing = setAuthorizingMap || setInternalAuthorizing;

  const statusQ = useQuery(
    ["hostAuth", "status", providersCsv],
    () => hostAuthAPI.status(providersCsv).then(extractDataFromResponse),
    { enabled: providers.length > 0, staleTime: 5000 },
  );

  // When server-union is enabled, load server-computed consent scopes per provider
  const consentQueriesArr = useQueries(
    (useServerUnionForAuthorize ? providers : []).map((prov) => ({
      queryKey: ["hostAuth", "consentScopes", prov],
      queryFn: () =>
        hostAuthAPI.consentScopes(prov).then(extractDataFromResponse),
      enabled: useServerUnionForAuthorize && !!prov,
      staleTime: 5000,
    })),
  );

  const consentScopesByProvider = useMemo(() => {
    if (!useServerUnionForAuthorize) {
      return {};
    }
    const map = {};
    (providers || []).forEach((prov, idx) => {
      const q = consentQueriesArr[idx];
      const scopes = Array.isArray(q?.data?.scopes) ? q.data.scopes : [];
      map[prov] = scopes;
    });
    return map;
  }, [providers, consentQueriesArr, useServerUnionForAuthorize]);

  const allConnected = useMemo(() => {
    if (!providers.length) {
      return true;
    }
    const s = statusQ.data || {};
    return providers.every((p) => {
      const connected = !!s[p]?.user_connected;
      const granted = Array.isArray(s[p]?.granted_scopes)
        ? s[p].granted_scopes
        : [];
      const desired = useServerUnionForAuthorize
        ? consentScopesByProvider[p] || []
        : scopesByProvider[p] || [];
      if (desired.length === 0) {
        return true;
      }
      const missing = desired.filter((sc) => !granted.includes(sc));
      // Only missing scopes matter - extra granted scopes are fine
      return connected && missing.length === 0;
    });
  }, [
    statusQ.data,
    providers,
    scopesByProvider,
    consentScopesByProvider,
    useServerUnionForAuthorize,
  ]);

  useEffect(() => {
    if (onStatusChange) {
      onStatusChange(allConnected, statusQ.data || {});
    }
  }, [allConnected, statusQ.data, onStatusChange]);

  const navigate = useNavigate();

  const { startAuthorize } = useOAuthAuthorize();
  const handleConnect = useCallback(async (provider, desiredScopes) => {
    try {
      await startAuthorize({
        provider,
        scopes: desiredScopes,
        onStart: () => setAuthorizing((m) => ({ ...m, [provider]: true })),
        onDone: () => setAuthorizing((m) => ({ ...m, [provider]: false })),
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('OAuth authorization failed', e);
      // Clear authorizing state on error since onDone won't be called
      setAuthorizing((m) => ({ ...m, [provider]: false }));
    }
  }, [setAuthorizing, startAuthorize]);

  if (!providers.length) {
    return null;
  }

  const status = statusQ.data || {};

  return (
    <Box sx={{ mt: 2 }}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
      >
        {providers.map((p) => {
          const connected = !!status[p]?.user_connected;
          const granted = Array.isArray(status[p]?.granted_scopes)
            ? status[p].granted_scopes
            : [];
          const desired = useServerUnionForAuthorize
            ? consentScopesByProvider[p] || []
            : scopesByProvider[p] || [];
          const missing = desired.filter((sc) => !granted.includes(sc));
          const extra = granted.filter((sc) => !desired.includes(sc));
          // Only missing scopes require reauthorization - extra granted scopes are fine
          const needReauth = desired.length > 0 && (missing.length > 0 || !connected);
          const label = `${p.charAt(0).toUpperCase()}${p.slice(1)}: ${connected ? 'Connected' : 'Not Connected'}`;
          const chipColor = connected ? (needReauth ? 'warning' : 'success') : 'default';
          const chipTooltip = [
            `Granted: ${(status[p]?.granted_scopes || []).join(', ')}`,
            desired.length ? `Desired: ${desired.join(', ')}` : null,
            missing.length > 0 ? `Missing scopes: ${missing.join(', ')}` : null,
            extra.length > 0 ? `Extra scopes (OK): ${extra.length}` : null,
          ].filter(Boolean).join('\n');
          return (
            <Stack key={p} direction="row" spacing={1} alignItems="center">
              <Tooltip title={chipTooltip}>
                <Chip color={chipColor} label={label} />
              </Tooltip>
              {needReauth && !hideConnectButton && (
                <Tooltip title={desired.length
                  ? `Authorize ${p} with selected scopes.\n${desired.join('\n')}`
                  : `Select plugin subscriptions for ${p} to request scopes before connecting.`}>
                  <span>
                    <Button size="small" variant="outlined" disabled={!!authorizing[p] || desired.length === 0} onClick={() => handleConnect(p, desired)}>
                      {authorizing[p] ? <CircularProgress size={14} /> : 'Authorize Selected Scopes'}
                    </Button>
                  </span>
                </Tooltip>
              )}
              {needReauth && showManageLink && (
                <Tooltip title="Open the Connected Accounts page to review or manage providers">
                  <Button
                    size="small"
                    variant="text"
                    onClick={() => navigate("/settings/connected-accounts")}
                  >
                    Manage in Connected Accounts
                  </Button>
                </Tooltip>
              )}
              {connected && showDisconnect && (
                <Tooltip title="Removes stored tokens and identities for this provider">
                  <span>
                    <Button
                      size="small"
                      variant="outlined"
                      color="warning"
                      onClick={async () => {
                        const ok = window.confirm(
                          `Disconnect ${p}? This will remove stored tokens and identities for this provider.`,
                        );
                        if (!ok) {
                          return;
                        }
                        try {
                          await hostAuthAPI.disconnect(p);
                          qc.invalidateQueries([
                            "hostAuth",
                            "status",
                            providersCsv,
                          ]);
                        } catch (e) {
                          // eslint-disable-next-line no-console
                          console.error("Disconnect failed", e);
                        }
                      }}
                    >
                      Disconnect
                    </Button>
                  </span>
                </Tooltip>
              )}
            </Stack>
          );
        })}
      </Stack>
    </Box>
  );
}
