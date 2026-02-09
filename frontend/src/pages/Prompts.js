/**
 * Prompts Management Page
 *
 * Standalone page for managing prompts across all entity types.
 * This is the central hub for creating, editing, and organizing prompts
 * for knowledge bases, LLM models, agents, workflows, and plugins.
 */

import React from 'react';
import { Container, Paper } from '@mui/material';
import TextSnippetIcon from '@mui/icons-material/TextSnippet';
import PromptManager from '../components/PromptManager';
import PageHelpHeader from '../components/PageHelpHeader';

function Prompts() {
  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <PageHelpHeader
        title="Prompts"
        description="Prompts define behavior for your AI assistant. System prompts set personality and instructions, while KB-specific prompts guide how knowledge base content is used in responses."
        icon={<TextSnippetIcon />}
        tips={[
          'Create a "system" prompt type to define your assistant\'s core behavior',
          'Assign prompts to Model Configurations to activate them',
          'Use KB-specific prompts to customize how documents from each knowledge base are presented',
          'Test prompts by attaching them to a Model Configuration and using the LLM Tester',
        ]}
      />

      <Paper sx={{ p: 0, overflow: 'hidden' }}>
        <PromptManager title="All Prompts" showEntityFilter={true} open={true} />
      </Paper>
    </Container>
  );
}

export default Prompts;
