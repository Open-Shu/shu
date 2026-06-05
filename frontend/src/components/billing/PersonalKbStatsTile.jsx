import { useQuery } from 'react-query';
import { Box, Card, CardContent, Grid, Skeleton, Stack, Typography } from '@mui/material';
import MenuBookIcon from '@mui/icons-material/MenuBook';

import { knowledgeBaseAPI, extractDataFromResponse } from '../../services/api';
import { formatFullTokens } from '../../utils/billingFormatters';

// getPersonal returns the caller's personal KB or null (SHU-817). Owner-scoped
// server-side, so no list scan and no id needed.
const fetchPersonalKb = () => knowledgeBaseAPI.getPersonal().then(extractDataFromResponse);

function Stat({ label, value }) {
  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 600 }}>
        {value}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
    </Box>
  );
}

/**
 * Shows the value of the user's Personal Knowledge Base (SHU-844): document
 * count, chunk count, and last-synced — all first-class KB columns, no JSON
 * digging. "How often your KB was cited in a response" is a follow-up that
 * needs a dedicated usage table. Renders a neutral state when the user has no
 * personal KB yet.
 */
export default function PersonalKbStatsTile() {
  const { data: kb, isLoading } = useQuery(['my-usage:personal-kb'], fetchPersonalKb, { staleTime: 60_000 });

  if (isLoading) {
    return <Skeleton variant="rounded" height={120} />;
  }

  if (!kb) {
    return (
      <Card variant="outlined">
        <CardContent>
          <Typography variant="body2" color="text.secondary">
            You don&apos;t have a Personal Knowledge Base yet. Attach the brain icon in chat to start building one.
          </Typography>
        </CardContent>
      </Card>
    );
  }

  const lastSynced = kb.last_sync_at ? new Date(kb.last_sync_at).toLocaleDateString() : 'never';

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
          <MenuBookIcon fontSize="small" color="primary" />
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            {kb.name || 'Personal Knowledge'}
          </Typography>
        </Stack>
        <Grid container spacing={2}>
          <Grid item xs={4}>
            <Stat label="Documents" value={formatFullTokens(kb.document_count ?? 0)} />
          </Grid>
          <Grid item xs={4}>
            <Stat label="Chunks" value={formatFullTokens(kb.total_chunks ?? 0)} />
          </Grid>
          <Grid item xs={4}>
            <Stat label="Last synced" value={lastSynced} />
          </Grid>
        </Grid>
      </CardContent>
    </Card>
  );
}
