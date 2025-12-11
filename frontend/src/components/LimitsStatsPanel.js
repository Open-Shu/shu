import React from 'react';
import { Box, Button, Card, CardContent, FormControl, InputLabel, MenuItem, Select, Stack, TextField, Typography } from '@mui/material';
import { useQuery } from 'react-query';
import { pluginsAPI } from '../services/pluginsApi';
import { extractDataFromResponse, formatError } from '../services/api';

export default function LimitsStatsPanel() {
  const [prefix, setPrefix] = React.useState('rl:plugin:');
  const [limit, setLimit] = React.useState(50);

  const { data, isLoading, error, refetch, isFetching } = useQuery(
    ['limits', 'stats', prefix, limit],
    () => pluginsAPI.getLimitsStats(prefix, limit).then(extractDataFromResponse),
    { keepPreviousData: true }
  );

  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Typography variant="h6">Limiter/Quota Stats</Typography>
          <Stack direction="row" spacing={1}>
            <FormControl size="small" sx={{ minWidth: 220 }}>
              <InputLabel id="prefix-label">Prefix</InputLabel>
              <Select labelId="prefix-label" label="Prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)}>
                <MenuItem value="rl:plugin:">rl:plugin: — per-user/per-plugin rate limits</MenuItem>
                <MenuItem value="quota:d:">quota:d: — daily quotas (reset end of day)</MenuItem>
                <MenuItem value="quota:m:">quota:m: — monthly quotas (reset end of month)</MenuItem>
              </Select>
            </FormControl>
            <TextField
              size="small"
              type="number"
              label="Max entries"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              sx={{ width: 100 }}
            />
            <Button variant="outlined" onClick={() => refetch()} disabled={isFetching}>Refresh</Button>
          </Stack>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Snapshot of raw Redis keys. Values and TTLs update live as requests flow. Use this for quick diagnostics; it is not an aggregated report.
        </Typography>

        {isLoading && <Typography variant="body2">Loading…</Typography>}
        {error && <Typography color="error">{formatError(error)}</Typography>}
        {data && Array.isArray(data.entries) && (
          <Box component="pre" sx={{ bgcolor: '#f8fafc', p: 1, borderRadius: 1, border: '1px solid #e2e8f0', maxHeight: 300, overflow: 'auto' }}>
            {JSON.stringify(data.entries, null, 2)}
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

