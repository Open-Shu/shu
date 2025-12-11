import React from 'react';
import { Box, Button, CircularProgress, Paper, Typography } from '@mui/material';

const PluginRunPanel = React.memo(function PluginRunPanel({ pluginRun, onClear }) {
  if (!pluginRun) {
    return null;
  }

  return (
    <Paper variant="outlined" sx={{ m: 1, p: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography variant="subtitle2">Plugin execution: {pluginRun.plugin?.name}</Typography>
        <Button size="small" onClick={onClear}>Clear</Button>
      </Box>
      {pluginRun.status === 'running' ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1 }}>
          <CircularProgress size={16} />
          <Typography variant="body2">Running…</Typography>
        </Box>
      ) : (
        <Box sx={{ mt: 1 }}>
          <Typography variant="caption">Result</Typography>
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>
            {(() => {
              try {
                return JSON.stringify(pluginRun.data, null, 2);
              } catch (error) {
                return String(pluginRun.data);
              }
            })()}
          </pre>
        </Box>
      )}
    </Paper>
  );
});

export default PluginRunPanel;
