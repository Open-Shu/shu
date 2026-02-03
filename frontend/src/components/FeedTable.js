import React from "react";
import {
  IconButton,
  Stack,
  Switch,
  Tooltip,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  Paper,
  FormControl,
  Select,
  MenuItem,
} from "@mui/material";
import PlayCircleOutlineIcon from "@mui/icons-material/PlayCircleOutline";
import HistoryIcon from "@mui/icons-material/History";
import EditIcon from "@mui/icons-material/Edit";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import { formatLocalShort } from "../utils/datetime";
import { pluginPrimaryLabel } from "../utils/plugins";

function formatInterval(seconds) {
  const s = Number(seconds || 0);
  if (s % 86400 === 0) {
    return `${s / 86400}d`;
  }
  if (s % 3600 === 0) {
    return `${s / 3600}h`;
  }
  if (s % 60 === 0) {
    return `${s / 60}m`;
  }
  return `${s}s`;
}

export default function FeedTable({
  rows = [],
  plugins = [],
  kbs = [],
  userOptions = [],
  intervalOptions = [900, 3600, 21600, 86400],
  showKbColumn = false,
  showRunsButton = true,
  onChangeKb = null,
  onChangeOwner = null,
  onChangeInterval = null,
  onToggleEnabled = null,
  onRunNow = null,
  onDelete = null,
  onEdit = null,
  onOpenRuns = null,
  disablePatch = false,
  disableRun = false,
  disableDelete = false,
}) {
  // Helper to get plugin display name
  const getPluginDisplayName = (pluginName) => {
    const plugin = plugins.find((p) => p.name === pluginName);
    return plugin ? pluginPrimaryLabel(plugin) : pluginName;
  };
  return (
    <TableContainer
      component={Paper}
      sx={{ maxWidth: "100%", overflowX: "auto" }}
    >
      <Table size="small" sx={{ tableLayout: "auto" }}>
        <TableHead>
          <TableRow>
            <TableCell sx={{ minWidth: 120 }}>Name</TableCell>
            <TableCell sx={{ minWidth: 100 }}>Plugin</TableCell>
            {showKbColumn && <TableCell sx={{ minWidth: 180 }}>KB</TableCell>}
            <TableCell sx={{ minWidth: 180 }}>Owner</TableCell>
            <TableCell sx={{ minWidth: 140 }}>Identity</TableCell>
            <TableCell align="right" sx={{ minWidth: 90 }}>
              Interval
            </TableCell>
            <TableCell align="right" sx={{ minWidth: 100 }}>
              Next Run
            </TableCell>
            <TableCell align="right" sx={{ minWidth: 100 }}>
              Last Run
            </TableCell>
            <TableCell align="center" sx={{ minWidth: 70 }}>
              Enabled
            </TableCell>
            <TableCell align="center" sx={{ minWidth: 140 }}>
              Actions
            </TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id} hover>
              <TableCell>{row.name}</TableCell>
              <TableCell>{getPluginDisplayName(row.plugin_name)}</TableCell>
              {showKbColumn && (
                <TableCell>
                  <FormControl size="small" sx={{ width: 180 }}>
                    <Select
                      displayEmpty
                      value={row.params?.kb_id || ""}
                      onChange={(e) =>
                        onChangeKb && onChangeKb(row, e.target.value || null)
                      }
                    >
                      <MenuItem value="">
                        <em>No KB</em>
                      </MenuItem>
                      {kbs.map((kb) => (
                        <MenuItem key={kb.id} value={kb.id}>
                          {kb.name || kb.id}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </TableCell>
              )}
              <TableCell>
                <FormControl size="small" sx={{ width: 180 }}>
                  <Select
                    value={row.owner_user_id || ""}
                    onChange={(e) =>
                      onChangeOwner &&
                      onChangeOwner(row, e.target.value || null)
                    }
                    displayEmpty
                    renderValue={(val) => {
                      if (!val) {
                        return "Unassigned";
                      }
                      const opt = userOptions.find(
                        (o) => String(o.id) === String(val),
                      );
                      return opt ? opt.label : val;
                    }}
                  >
                    <MenuItem value="">
                      <em>Unassigned</em>
                    </MenuItem>
                    {userOptions.map((o) => (
                      <MenuItem key={o.id} value={o.id}>
                        {o.label}
                      </MenuItem>
                    ))}
                    {!userOptions.some(
                      (o) => String(o.id) === String(row.owner_user_id),
                    ) &&
                      row.owner_user_id && (
                        <MenuItem value={row.owner_user_id}>
                          {row.owner_user_id}
                        </MenuItem>
                      )}
                  </Select>
                </FormControl>
              </TableCell>
              <TableCell>
                {(() => {
                  const st = String(row.identity_status || "").toLowerCase();
                  const labelMap = {
                    connected: "Connected",
                    missing_identity: "Missing",
                    delegation: "Delegation (SA)",
                    delegation_subject_missing: "Delegation: set email",
                    delegation_denied: "Delegation denied",
                    no_owner: "No Owner",
                    unknown: "Unknown",
                  };
                  const tooltipMap = {
                    connected: "User OAuth connected",
                    missing_identity: "Connect account in Connected Accounts",
                    delegation: "Delegation (service account)",
                    delegation_subject_missing:
                      "Set impersonation email in feed config",
                    delegation_denied:
                      "Fix service account delegation or scopes",
                    no_owner: "Assign an owner to this feed",
                    unknown: "Unknown identity status",
                  };
                  const colorMap = {
                    connected: "success",
                    missing_identity: "default",
                    delegation: "info",
                    delegation_subject_missing: "warning",
                    delegation_denied: "error",
                    no_owner: "default",
                    unknown: "default",
                  };
                  const label = labelMap[st] || "Unknown";
                  const color = colorMap[st] || "default";
                  const tip = tooltipMap[st] || "";
                  return (
                    <Tooltip title={tip}>
                      <Chip size="small" color={color} label={label} />
                    </Tooltip>
                  );
                })()}
              </TableCell>
              <TableCell align="right">
                <FormControl size="small" sx={{ width: 90 }}>
                  <Select
                    value={row.interval_seconds || 3600}
                    onChange={(e) =>
                      onChangeInterval &&
                      onChangeInterval(row, Number(e.target.value))
                    }
                  >
                    {intervalOptions.map((s) => (
                      <MenuItem key={s} value={s}>
                        {formatInterval(s)}
                      </MenuItem>
                    ))}
                    {!intervalOptions.includes(row.interval_seconds) && (
                      <MenuItem value={row.interval_seconds}>
                        {formatInterval(row.interval_seconds)}
                      </MenuItem>
                    )}
                  </Select>
                </FormControl>
              </TableCell>
              <TableCell align="right">
                {formatLocalShort(row.next_run_at)}
              </TableCell>
              <TableCell align="right">
                {formatLocalShort(row.last_run_at)}
              </TableCell>
              <TableCell align="center">
                <Switch
                  checked={!!row.enabled}
                  onChange={() => onToggleEnabled && onToggleEnabled(row)}
                />
              </TableCell>
              <TableCell align="center">
                <Stack direction="row" spacing={1} justifyContent="center">
                  {onEdit && (
                    <Tooltip title="Edit feed">
                      <span>
                        <IconButton
                          onClick={() => onEdit(row)}
                          disabled={disablePatch}
                        >
                          <EditIcon />
                        </IconButton>
                      </span>
                    </Tooltip>
                  )}
                  <Tooltip title="Run now">
                    <span>
                      <IconButton
                        onClick={() => onRunNow && onRunNow(row)}
                        disabled={!row.enabled || disablePatch || disableRun}
                      >
                        <PlayCircleOutlineIcon />
                      </IconButton>
                    </span>
                  </Tooltip>
                  {showRunsButton && onOpenRuns && (
                    <Tooltip title="Recent runs">
                      <span>
                        <IconButton onClick={() => onOpenRuns(row)}>
                          <HistoryIcon />
                        </IconButton>
                      </span>
                    </Tooltip>
                  )}
                  <Tooltip title="Delete feed">
                    <span>
                      <IconButton
                        color="error"
                        onClick={() => onDelete && onDelete(row)}
                        disabled={disableDelete}
                      >
                        <DeleteOutlineIcon />
                      </IconButton>
                    </span>
                  </Tooltip>
                </Stack>
              </TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={showKbColumn ? 9 : 8}>
                <Typography variant="body2" color="text.secondary">
                  No feeds found.
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
