import React, { useMemo, useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  List,
  ListItemButton,
  ListItemText,
  CircularProgress,
  Tooltip,
} from "@mui/material";
import { useQuery } from "react-query";
import { pluginsAPI } from "../services/pluginsApi";
import { extractDataFromResponse, formatError } from "../services/api";
import { pluginDisplayName, pluginPrimaryLabel } from "../utils/plugins";

export default function PluginPickerDialog({ open, onClose, onSelect }) {
  const [filter, setFilter] = useState("");
  const { data, isLoading, isFetching, error } = useQuery(
    ["plugins", "list"],
    () => pluginsAPI.list().then(extractDataFromResponse),
    { enabled: open, staleTime: 10000 },
  );

  const tools = useMemo(() => (Array.isArray(data) ? data : []), [data]);
  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) {
      return tools;
    }
    return tools.filter(
      (t) =>
        (pluginDisplayName(t) || "").toLowerCase().includes(f) ||
        (t.name || "").toLowerCase().includes(f),
    );
  }, [tools, filter]);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Select a Plugin</DialogTitle>
      <DialogContent dividers>
        {isLoading || isFetching ? (
          <CircularProgress size={20} />
        ) : error ? (
          <div style={{ color: "red" }}>{formatError(error)}</div>
        ) : (
          <>
            <Tooltip title="Type to filter by plugin name. Use slash-command in chat input: e.g., /gmail_digest">
              <TextField
                fullWidth
                size="small"
                placeholder="Filter plugins by name"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                sx={{ mb: 2 }}
              />
            </Tooltip>
            <List dense>
              {filtered.map((t) => (
                <ListItemButton
                  key={t.name}
                  onClick={() => onSelect && onSelect(t)}
                >
                  <ListItemText
                    primary={pluginPrimaryLabel(t)}
                    secondary={`/${t.name} • version: ${t.version || "n/a"}${t.capabilities?.length ? " • " + t.capabilities.join(", ") : ""}`}
                  />
                </ListItemButton>
              ))}
            </List>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
}
