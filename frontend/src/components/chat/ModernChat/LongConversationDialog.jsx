import React from 'react';
import { Box, Button, Stack, Typography } from '@mui/material';

const LongConversationBanner = React.memo(function LongConversationBanner({
  open,
  messageCount,
  onStartNew,
  onDismiss,
}) {
  if (!open) {
    return null;
  }

  return (
    <Box sx={{ px: 2, py: 1.5 }}>
      <Typography variant="subtitle2" gutterBottom>
        Conversation Getting Long
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        This conversation has {messageCount} messages. Long conversations can affect response quality and performance.
      </Typography>
      <Stack direction="row" spacing={1} justifyContent="flex-end">
        <Button size="small" onClick={onDismiss}>
          Continue Anyway
        </Button>
        <Button size="small" variant="contained" onClick={onStartNew}>
          Start New Conversation
        </Button>
      </Stack>
    </Box>
  );
});

export default LongConversationBanner;
