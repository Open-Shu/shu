import React from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import {
  Box,
  Button,
  Card,
  CardContent,
  Grid,
  Stack,
  TextField,
  Typography,
  IconButton,
  Tooltip,
  Alert,
  ToggleButtonGroup,
  ToggleButton,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import { pluginsAPI } from '../services/pluginsApi';
import { extractDataFromResponse, formatError } from '../services/api';

export default function PluginSecretsEditor({ name }) {
  const qc = useQueryClient();
  const [scope, setScope] = React.useState('system'); // 'system' or 'user'
  const [userId, setUserId] = React.useState('');
  const [newKey, setNewKey] = React.useState('');
  const [newVal, setNewVal] = React.useState('');

  const isSystemScope = scope === 'system';
  const queryEnabled = !!name && (isSystemScope || !!userId);

  const { data, isLoading, error, refetch, isFetching } = useQuery(
    ['plugins', 'secrets', name, scope, userId],
    () => pluginsAPI.listSecrets(name, isSystemScope ? null : userId, scope).then(extractDataFromResponse),
    { enabled: queryEnabled }
  );

  const setMut = useMutation(
    () =>
      pluginsAPI.setSecret(name, newKey, isSystemScope ? null : userId, newVal, scope).then(extractDataFromResponse),
    {
      onSuccess: () => {
        setNewVal('');
        setNewKey('');
        qc.invalidateQueries(['plugins', 'secrets', name, scope, userId]);
      },
    }
  );

  const delMut = useMutation(
    ({ key }) => pluginsAPI.deleteSecret(name, key, isSystemScope ? null : userId, scope).then(extractDataFromResponse),
    {
      onSuccess: () => {
        qc.invalidateQueries(['plugins', 'secrets', name, scope, userId]);
      },
    }
  );

  const keys = data?.keys || [];

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={2}>
          <Typography variant="h6">Secrets</Typography>
          <Typography variant="body2" color="text.secondary">
            Manage encrypted secrets for this plugin. System secrets are shared defaults; user secrets override them
            per-user.
          </Typography>

          <Stack direction="row" spacing={2} alignItems="center">
            <ToggleButtonGroup value={scope} exclusive onChange={(e, val) => val && setScope(val)} size="small">
              <ToggleButton value="system">System (Shared)</ToggleButton>
              <ToggleButton value="user">User (Per-User)</ToggleButton>
            </ToggleButtonGroup>
          </Stack>

          {!isSystemScope && (
            <Grid container spacing={2}>
              <Grid item xs={12} sm={6} md={4}>
                <TextField
                  label="User ID"
                  fullWidth
                  value={userId}
                  onChange={(e) => setUserId(e.target.value)}
                  placeholder="UUID or user ID"
                />
              </Grid>
              <Grid item xs={12} sm={6} md={2}>
                <Button variant="outlined" onClick={() => refetch()} disabled={!userId || isFetching}>
                  Load
                </Button>
              </Grid>
            </Grid>
          )}

          {error && <Typography color="error">{formatError(error)}</Typography>}
          {(isLoading || isFetching) && queryEnabled && (
            <Typography variant="body2" color="text.secondary">
              Loading {scope} secrets...
            </Typography>
          )}

          {queryEnabled && (
            <>
              <Typography variant="subtitle2">Existing keys ({scope} scope)</Typography>
              {keys.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No keys found
                </Typography>
              ) : (
                <Box>
                  {keys.map((k) => (
                    <Box
                      key={k}
                      display="flex"
                      alignItems="center"
                      justifyContent="space-between"
                      sx={{
                        border: '1px solid #e2e8f0',
                        borderRadius: 1,
                        p: 1,
                        mb: 1,
                      }}
                    >
                      <Typography>{k}</Typography>
                      <Tooltip title="Delete secret key">
                        <span>
                          <IconButton
                            size="small"
                            color="error"
                            onClick={() => delMut.mutate({ key: k })}
                            disabled={delMut.isLoading}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                    </Box>
                  ))}
                </Box>
              )}

              <Typography variant="subtitle2" sx={{ mt: 2 }}>
                Add or update a secret
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={12} sm={4}>
                  <TextField
                    label="Key"
                    fullWidth
                    value={newKey}
                    onChange={(e) => setNewKey(e.target.value)}
                    placeholder="e.g., api_key"
                  />
                </Grid>
                <Grid item xs={12} sm={6}>
                  <TextField
                    label="Value"
                    type="password"
                    fullWidth
                    value={newVal}
                    onChange={(e) => setNewVal(e.target.value)}
                  />
                </Grid>
                <Grid item xs={12} sm={2}>
                  <Button
                    variant="contained"
                    onClick={() => setMut.mutate()}
                    disabled={!newKey || !newVal || setMut.isLoading}
                  >
                    Save
                  </Button>
                </Grid>
              </Grid>
              {setMut.error && (
                <Alert severity="error" sx={{ mt: 1 }}>
                  Failed to save: {formatError(setMut.error)}
                </Alert>
              )}
            </>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}
