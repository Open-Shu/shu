import React, { useMemo, useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import AddIcon from '@mui/icons-material/Add';
import ScheduleIcon from '@mui/icons-material/Schedule';
import { useMutation, useQuery, useQueryClient } from 'react-query';
import { extractDataFromResponse, formatError, authAPI } from '../services/api';
import { schedulesAPI } from '../services/schedulesApi';
import { pluginsAPI } from '../services/pluginsApi';
import FeedCreateDialog from './FeedCreateDialog';
import FeedEditDialog from './FeedEditDialog';
import FeedTable from './FeedTable';
import RecentRunsDialog from './RecentRunsDialog';
import PageHelpHeader from './PageHelpHeader';

export default function KBPluginFeedsTab({ knowledgeBaseId }) {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [runsOpenFor, setRunsOpenFor] = useState(null);

  const pluginsQ = useQuery(['plugins','list'], () => pluginsAPI.list().then(extractDataFromResponse), { staleTime: 10000 });
  const usersQ = useQuery(['users','list'], () => authAPI.getUsers().then(extractDataFromResponse), { staleTime: 10000 });
  const users = Array.isArray(usersQ.data) ? usersQ.data : [];
  const getUserLabel = (u) => (u.email || u.name || u.user_id || u.id);
  const userOptions = users.map(u => ({ id: u.user_id || u.id, label: getUserLabel(u) }));
  const intervalOptions = [900, 3600, 21600, 86400];

  const { data, isLoading, isFetching, error, refetch } = useQuery(
    ['schedules', 'list', { kb_id: knowledgeBaseId }],
    () => schedulesAPI.list({ kb_id: knowledgeBaseId }).then(extractDataFromResponse),
    { enabled: !!knowledgeBaseId, staleTime: 5000 }
  );

  const schedules = useMemo(() => {
    const raw = Array.isArray(data) ? data : [];
    return raw.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  }, [data]);

  const patchMut = useMutation(
    ({ id, payload }) => schedulesAPI.update(id, payload).then(extractDataFromResponse),
    { onSuccess: () => qc.invalidateQueries(['schedules','list']) }
  );

  const deleteMut = useMutation(
    (id) => schedulesAPI.delete(id).then(extractDataFromResponse),
    { onSuccess: () => {
      qc.invalidateQueries(['schedules']);
      qc.invalidateQueries(['schedules','list']);
    }}
  );

  const runPendingMut = useMutation((opts) => schedulesAPI.runPending(opts).then(extractDataFromResponse));

  const handleToggleEnabled = (row) => {
    patchMut.mutate({ id: row.id, payload: { enabled: !row.enabled } });
  };

  const handleRunNow = (row) => {
    schedulesAPI.runNow(row.id).then(extractDataFromResponse).then(() => {
      runPendingMut.mutate({ limit: 1, schedule_id: row.id });
    });
  };

  const handleDelete = (row) => {
    if (!row?.id) return;
    const ok = window.confirm(`Delete feed "${row.name}"? This detaches existing run history but does not delete it.`);
    if (!ok) return;
    deleteMut.mutate(row.id);
  };

  return (
    <Box>
      <PageHelpHeader
        title="Plugin Feeds for this Knowledge Base"
        description="Plugin Feeds automate data ingestion by running plugin operations on a schedule. Feeds created here are pre-configured to target this specific Knowledge Base."
        icon={<ScheduleIcon />}
        tips={[
          'Create a feed to automatically sync data from email, calendar, drive, or other connected services',
          'Each feed runs as a specific user—ensure that user has authorized the required plugin',
          'Use "Run Now" to manually trigger a feed for testing before enabling the schedule',
          'View recent runs via the clock icon to troubleshoot execution issues',
        ]}
      />

      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={2} mt={2}>
        <Typography variant="h6">Feeds targeting this KB</Typography>
        <Stack direction="row" spacing={1}>
          <Tooltip title="Reload list">
            <span>
              <Button variant="outlined" startIcon={<RefreshIcon />} onClick={() => refetch()} disabled={isFetching}>Refresh</Button>
            </span>
          </Tooltip>
          <Tooltip title="Create a feed for this KB">
            <span>
              <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>Create Feed</Button>
            </span>
          </Tooltip>
        </Stack>
      </Stack>

      {isLoading && (
        <Box display="flex" alignItems="center" gap={1}><CircularProgress size={20} /> Loading…</Box>
      )}
      {error && (
        <Typography color="error">{formatError(error)}</Typography>
      )}

      <FeedTable
        rows={schedules}
        plugins={(Array.isArray(pluginsQ.data) ? pluginsQ.data : [])}
        userOptions={userOptions}
        intervalOptions={intervalOptions}
        showKbColumn={false}
        showRunsButton
        onChangeOwner={(row, ownerId) => patchMut.mutate({ id: row.id, payload: { owner_user_id: ownerId || null } })}
        onChangeInterval={(row, seconds) => patchMut.mutate({ id: row.id, payload: { interval_seconds: Number(seconds) } })}
        onToggleEnabled={handleToggleEnabled}
        onRunNow={handleRunNow}
        onDelete={handleDelete}
        onEdit={(row) => { setEditing(row); setEditOpen(true); }}
        onOpenRuns={(row) => setRunsOpenFor(row)}
        disablePatch={patchMut.isLoading}
        disableRun={runPendingMut.isLoading}
        disableDelete={deleteMut.isLoading}
      />

      <FeedCreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          qc.invalidateQueries(['schedules']);
          refetch();
        }}
        lockedKbId={knowledgeBaseId}
      />
      <FeedEditDialog open={editOpen} onClose={() => { setEditOpen(false); setEditing(null); }} schedule={editing} />
      <RecentRunsDialog open={!!runsOpenFor} schedule={runsOpenFor} onClose={() => setRunsOpenFor(null)} />
    </Box>
  );
}

