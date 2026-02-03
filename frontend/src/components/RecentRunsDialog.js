import React from "react";
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Stack,
  Typography,
  Chip,
} from "@mui/material";
import { useQuery } from "react-query";
import { extractDataFromResponse, formatError } from "../services/api";
import { schedulesAPI } from "../services/schedulesApi";
import { formatLocalShort } from "../utils/datetime";

export default function RecentRunsDialog({ open, schedule, onClose }) {
  const runsQ = useQuery(
    ["executions", "schedule", schedule?.id || null],
    () =>
      schedulesAPI
        .listExecutions({ schedule_id: schedule.id, limit: 10 })
        .then(extractDataFromResponse),
    { enabled: !!schedule && open, staleTime: 2000 },
  );

  return (
    <Dialog open={!!open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        Recent Runs {schedule ? `(${schedule.name})` : ""}
      </DialogTitle>
      <DialogContent dividers>
        {!schedule ? null : (
          <>
            {runsQ.isLoading && (
              <Box display="flex" alignItems="center" gap={1}>
                <CircularProgress size={18} /> Loading…
              </Box>
            )}
            {runsQ.isError && (
              <Typography color="error">{formatError(runsQ.error)}</Typography>
            )}
            {Array.isArray(runsQ.data) && runsQ.data.length === 0 && (
              <Typography color="text.secondary">No runs yet.</Typography>
            )}
            {Array.isArray(runsQ.data) &&
              runsQ.data.map((r) => (
                <Box
                  key={r.id}
                  sx={{
                    mb: 1,
                    p: 1,
                    border: "1px solid #eee",
                    borderRadius: 1,
                  }}
                >
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    justifyContent="space-between"
                  >
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Chip
                        size="small"
                        label={r.status}
                        color={
                          r.status === "completed"
                            ? "success"
                            : r.status === "failed"
                              ? "error"
                              : "default"
                        }
                      />
                      <Typography variant="body2">
                        {formatLocalShort(
                          r.completed_at || r.started_at || r.created_at,
                        )}
                      </Typography>
                    </Stack>
                    <Stack direction="row" spacing={1}>
                      {r.error && (
                        <Typography variant="body2" color="error">
                          {String(r.error).slice(0, 140)}
                        </Typography>
                      )}
                    </Stack>
                  </Stack>
                  {r.result && (
                    <Box sx={{ mt: 1 }}>
                      <Typography variant="caption" color="text.secondary">
                        Result
                      </Typography>
                      <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                        {JSON.stringify(r.result, null, 2)}
                      </pre>
                    </Box>
                  )}
                </Box>
              ))}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button
          onClick={() => schedule && runsQ.refetch()}
          disabled={!schedule || runsQ.isFetching}
        >
          Refresh
        </Button>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
