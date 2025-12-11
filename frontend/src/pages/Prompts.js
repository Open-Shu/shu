/**
 * Prompts Management Page
 * 
 * Standalone page for managing prompts across all entity types.
 * This is the central hub for creating, editing, and organizing prompts
 * for knowledge bases, LLM models, agents, workflows, and plugins.
 */

import React from 'react';
import {
  Box,
  Container,
  Typography,
  Paper
} from '@mui/material';
import PromptManager from '../components/PromptManager';

function Prompts() {
  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Box mb={4}>
        <Typography variant="h4" component="h1" gutterBottom>
          Prompt Management
        </Typography>
        <Typography variant="body1" color="text.secondary" paragraph>
          Create and manage prompts for knowledge bases, LLM models, agents, workflows, and plugins.
          Prompts define how different components of the system behave and interact.
        </Typography>
      </Box>

      <Paper sx={{ p: 0, overflow: 'hidden' }}>
        <PromptManager
          title="All Prompts"
          showEntityFilter={true}
          open={true}
        />
      </Paper>
    </Container>
  );
}

export default Prompts;
