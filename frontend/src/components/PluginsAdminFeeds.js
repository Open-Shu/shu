import React, { useMemo, useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Tooltip,
  Typography,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import AddIcon from '@mui/icons-material/Add';
import { useMutation, useQuery, useQueryClient } from 'react-query';
import {
  extractDataFromResponse,
  extractItemsFromResponse,
  formatError,
  authAPI,
  knowledgeBaseAPI,
} from '../services/api';
import { schedulesAPI } from '../services/schedulesApi';
import { pluginsAPI } from '../services/pluginsApi';
import FeedCreateDialog from './FeedCreateDialog';
import FeedEditDialog from './FeedEditDialog';
import FeedTable from './FeedTable';
import RecentRunsDialog from './RecentRunsDialog';
import PageHelpHeader from './PageHelpHeader';
import ScheduleIcon from '@mui/icons-material/Schedule';

export default function PluginsAdminFeeds() {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [pluginFilter, setPluginFilter] = useState('');

  const [ownerFilter, setOwnerFilter] = useState('');
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [runsOpenFor, setRunsOpenFor] = useState(null);

  const [kbFilter, setKbFilter] = useState('');

  const pluginsQ = useQuery(['plugins', 'list'], () => pluginsAPI.list().then(extractDataFromResponse), {
    staleTime: 10000,
  });
  const usersQ = useQuery(['users', 'list'], () => authAPI.getUsers().then(extractDataFromResponse), {
    staleTime: 10000,
  });
  const kbsQ = useQuery(['kbs', 'list'], () => knowledgeBaseAPI.list().then(extractItemsFromResponse), {
    staleTime: 10000,
  });
  const users = Array.isArray(usersQ.data) ? usersQ.data : [];
  const getUserLabel = (u) => u.email || u.name || u.user_id || u.id;
  const userOptions = users.map((u) => ({
    id: u.user_id || u.id,
    label: getUserLabel(u),
  }));
  const intervalOptions = [900, 3600, 21600, 86400];

  const { data, isLoading, isFetching, error, refetch } = useQuery(
    ['schedules', 'list', { pluginFilter, ownerFilter, kbFilter }],
    () =>
      schedulesAPI
        .list({
          ...(pluginFilter ? { plugin_name: pluginFilter } : {}),
          ...(ownerFilter ? { owner_user_id: ownerFilter } : {}),
          ...(kbFilter ? { kb_id: kbFilter } : {}),
        })
        .then(extractDataFromResponse),
    { staleTime: 5000 }
  );

  const schedules = useMemo(() => {
    const raw = Array.isArray(data) ? data : [];
    // Sort by creation time (stable order) to prevent rows jumping around on edits
    return raw.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  }, [data]);

  const patchMut = useMutation(({ id, payload }) => schedulesAPI.update(id, payload).then(extractDataFromResponse), {
    onSuccess: () => qc.invalidateQueries(['schedules', 'list']),
  });

  const deleteMut = useMutation((id) => schedulesAPI.delete(id).then(extractDataFromResponse), {
    onSuccess: () => qc.invalidateQueries(['schedules', 'list']),
  });

  const runDueMut = useMutation(() => schedulesAPI.runDue().then(extractDataFromResponse));

  const runPendingMut = useMutation((opts) => schedulesAPI.runPending(opts).then(extractDataFromResponse));

  const handleToggleEnabled = (row) => {
    patchMut.mutate({ id: row.id, payload: { enabled: !row.enabled } });
  };

  const handleRunNow = (row) => {
    // Enqueue only this schedule and run only its pending execution
    schedulesAPI
      .runNow(row.id)
      .then(extractDataFromResponse)
      .then(() => {
        runPendingMut.mutate(
          { limit: 1, schedule_id: row.id },
          {
            onSuccess: () => qc.invalidateQueries(['executions', 'schedule', row.id]),
          }
        );
      });
  };

  const handleDelete = (row) => {
    if (!row?.id) {
      return;
    }
    const ok = window.confirm(`Delete feed "${row.name}"? This detaches existing run history but does not delete it.`);
    if (!ok) {
      return;
    }
    deleteMut.mutate(row.id);
  };

  return (
    <Box p={3}>
      <PageHelpHeader
        title="Plugin Feeds"
        description="Plugin Feeds automate data synchronization by running plugin operations on a schedule. Feeds pull data from external services (email, calendar, drive, etc.) and ingest it into Knowledge Bases for searchable retrieval."
        icon={<ScheduleIcon />}
        tips={[
          'Create a feed by selecting a plugin, operation, target KB, and schedule interval',
          'Use "Run Now" to manually trigger a feed execution for testing',
          'Use "Run Due" to trigger all feeds that are past their scheduled time',
          'Each feed runs as a specific user—ensure that user has authorized the required plugin',
          'View recent runs by clicking the clock icon to troubleshoot execution issues',
        ]}
      />
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={2}>
        <Box />
        <Stack direction="row" spacing={1}>
          <Tooltip title="Reload list">
            <span>
              <Button variant="outlined" startIcon={<RefreshIcon />} onClick={() => refetch()} disabled={isFetching}>
                Refresh
              </Button>
            </span>
          </Tooltip>
          <Tooltip title="Enqueue due feeds">
            <span>
              <Button variant="outlined" onClick={() => runDueMut.mutate()} disabled={runDueMut.isLoading}>
                Run Due
              </Button>
            </span>
          </Tooltip>
          <Tooltip title="Run pending executions (development)">
            <span>
              <Button
                variant="outlined"
                onClick={() => runPendingMut.mutate({ limit: 5 })}
                disabled={runPendingMut.isLoading}
              >
                Run Pending
              </Button>
            </span>
          </Tooltip>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>
            Create Feed
          </Button>
        </Stack>
      </Stack>

      <Stack direction="row" spacing={2} alignItems="center" mb={2}>
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel id="plugin-filter-label">Plugin</InputLabel>
          <Select
            labelId="plugin-filter-label"
            label="Plugin"
            value={pluginFilter}
            onChange={(e) => setPluginFilter(e.target.value)}
          >
            <MenuItem value="">
              <em>All</em>
            </MenuItem>
            {(Array.isArray(pluginsQ.data) ? pluginsQ.data : []).map((t) => (
              <MenuItem key={t.name} value={t.name}>
                {t.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <TextField
          size="small"
          label="Owner User ID"
          value={ownerFilter}
          onChange={(e) => setOwnerFilter(e.target.value)}
        />
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel id="kb-filter-label">Knowledge Base</InputLabel>
          <Select
            labelId="kb-filter-label"
            label="Knowledge Base"
            value={kbFilter}
            onChange={(e) => setKbFilter(e.target.value)}
          >
            <MenuItem value="">
              <em>All</em>
            </MenuItem>
            {(Array.isArray(kbsQ.data) ? kbsQ.data : []).map((kb) => (
              <MenuItem key={kb.id} value={kb.id}>
                {kb.name || kb.id}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      {isLoading && (
        <Box display="flex" alignItems="center" gap={1}>
          <CircularProgress size={20} /> Loading…
        </Box>
      )}
      {error && <Typography color="error">{formatError(error)}</Typography>}

      <FeedTable
        rows={schedules}
        plugins={Array.isArray(pluginsQ.data) ? pluginsQ.data : []}
        kbs={Array.isArray(kbsQ.data) ? kbsQ.data : []}
        userOptions={userOptions}
        intervalOptions={intervalOptions}
        showKbColumn
        showRunsButton
        onChangeKb={(row, kbId) =>
          patchMut.mutate({
            id: row.id,
            payload: { params: { ...(row.params || {}), kb_id: kbId || null } },
          })
        }
        onChangeOwner={(row, ownerId) =>
          patchMut.mutate({
            id: row.id,
            payload: { owner_user_id: ownerId || null },
          })
        }
        onChangeInterval={(row, seconds) =>
          patchMut.mutate({
            id: row.id,
            payload: { interval_seconds: Number(seconds) },
          })
        }
        onToggleEnabled={handleToggleEnabled}
        onRunNow={handleRunNow}
        onDelete={handleDelete}
        onEdit={(row) => {
          setEditing(row);
          setEditOpen(true);
        }}
        onOpenRuns={(row) => setRunsOpenFor(row)}
        disablePatch={patchMut.isLoading}
        disableRun={runDueMut.isLoading}
        disableDelete={deleteMut.isLoading}
      />

      <FeedCreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => qc.invalidateQueries(['schedules', 'list'])}
      />
      <FeedEditDialog
        open={editOpen}
        onClose={() => {
          setEditOpen(false);
          setEditing(null);
        }}
        schedule={editing}
      />

      <RecentRunsDialog open={!!runsOpenFor} schedule={runsOpenFor} onClose={() => setRunsOpenFor(null)} />
    </Box>
  );
}
