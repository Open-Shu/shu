import React, { useMemo, useState } from 'react';
import { Box, Button, Card, CardContent, Grid, Stack, TextField, Typography } from '@mui/material';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { extractDataFromResponse, formatError } from '../services/api';
import { pluginsAPI } from '../services/pluginsApi';

export default function PluginLimitsEditor({ name }) {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery(
    ['plugins', 'limits', name],
    () => pluginsAPI.getLimits(name).then(extractDataFromResponse),
    { enabled: !!name }
  );

  const initial = useMemo(() => ({
    rate_limit_user_requests: data?.limits?.rate_limit_user_requests ?? '',
    rate_limit_user_period: data?.limits?.rate_limit_user_period ?? '',
    quota_daily_requests: data?.limits?.quota_daily_requests ?? '',
    quota_monthly_requests: data?.limits?.quota_monthly_requests ?? '',
    // Provider caps (shared across plugins that declare the same provider)
    provider_name: data?.limits?.provider_name ?? '',
    provider_rpm: data?.limits?.provider_rpm ?? '',
    provider_window_seconds: data?.limits?.provider_window_seconds ?? '',
    provider_concurrency: data?.limits?.provider_concurrency ?? '',
  }), [data]);

  const [form, setForm] = useState(initial);
  React.useEffect(() => setForm(initial), [initial]);

  // Numeric field setter
  const setField = (k) => (e) => {
    const v = e.target.value;
    setForm((prev) => ({ ...prev, [k]: v === '' ? '' : Number(v) }));
  };
  // Text field setter (no number coercion)
  const setTextField = (k) => (e) => {
    const v = e.target.value;
    setForm((prev) => ({ ...prev, [k]: v }));
  };


  const mutation = useMutation(
    () => pluginsAPI.setLimits(name, {
      rate_limit_user_requests: form.rate_limit_user_requests === '' ? undefined : form.rate_limit_user_requests,
      rate_limit_user_period: form.rate_limit_user_period === '' ? undefined : form.rate_limit_user_period,
      quota_daily_requests: form.quota_daily_requests === '' ? undefined : form.quota_daily_requests,
      quota_monthly_requests: form.quota_monthly_requests === '' ? undefined : form.quota_monthly_requests,
      // Provider caps
      provider_name: form.provider_name === '' ? undefined : form.provider_name,
      provider_rpm: form.provider_rpm === '' ? undefined : form.provider_rpm,
      provider_window_seconds: form.provider_window_seconds === '' ? undefined : form.provider_window_seconds,
      provider_concurrency: form.provider_concurrency === '' ? undefined : form.provider_concurrency,
    }).then(extractDataFromResponse),
    {
      onSuccess: () => {
        qc.invalidateQueries(['plugins', 'limits', name]);
      }
    }
  );

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={2}>
          <Typography variant="h6">Limits & Quotas</Typography>
          <Typography variant="body2" color="text.secondary">
            These overrides apply per plugin/version and per user. Leave a field blank to keep the existing value.
            Set 0 to disable a limit (e.g., disable quotas).
          </Typography>
          {isLoading && <Typography variant="body2">Loading limits…</Typography>}
          {error && <Typography color="error">{formatError(error)}</Typography>}
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="Rate limit: requests"
                type="number"
                fullWidth
                value={form.rate_limit_user_requests}
                onChange={setField('rate_limit_user_requests')}
                helperText="0 disables; leave blank to keep unchanged"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="Rate limit: period (sec)"
                type="number"
                fullWidth
                value={form.rate_limit_user_period}
                onChange={setField('rate_limit_user_period')}
                helperText="Window in seconds"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="Daily quota"
                type="number"
                fullWidth
                value={form.quota_daily_requests}
                onChange={setField('quota_daily_requests')}
                helperText="0 disables"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="Monthly quota"
                type="number"
                fullWidth
                value={form.quota_monthly_requests}
                onChange={setField('quota_monthly_requests')}
                helperText="0 disables"
              />
            </Grid>
          </Grid>
          <Typography variant="subtitle1" sx={{ mt: 2 }}>Provider caps</Typography>
          <Typography variant="body2" color="text.secondary">
            These limits are enforced at the provider/model level and shared across all plugins using the same provider_name.
          </Typography>
          <Grid container spacing={2} sx={{ mt: 1 }}>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="Provider name"
                fullWidth
                value={form.provider_name}
                onChange={setTextField('provider_name')}
                placeholder="e.g., openai:gpt-4o"
                helperText="String key identifying the provider/model"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="Provider RPM"
                type="number"
                fullWidth
                value={form.provider_rpm}
                onChange={setField('provider_rpm')}
                helperText="Requests per window"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="Provider window (sec)"
                type="number"
                fullWidth
                value={form.provider_window_seconds}
                onChange={setField('provider_window_seconds')}
                helperText="Window seconds for RPM"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="Provider concurrency"
                type="number"
                fullWidth
                value={form.provider_concurrency}
                onChange={setField('provider_concurrency')}
                helperText="Max in-flight requests"
              />
            </Grid>
          </Grid>

          <Box>
            <Button variant="contained" onClick={() => mutation.mutate()} disabled={mutation.isLoading}>
              {mutation.isLoading ? 'Saving…' : 'Save'}
            </Button>
            {mutation.error && (
              <Typography color="error" sx={{ mt: 1 }}>{formatError(mutation.error)}</Typography>
            )}
          </Box>
        </Stack>
      </CardContent>
    </Card>
  );
}

