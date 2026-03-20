import { useMemo } from 'react';
import { useQuery } from 'react-query';
import { Alert, Divider, FormControl, InputLabel, MenuItem, Select, Stack, TextField, Typography } from '@mui/material';
import { promptAPI } from '../../api/prompts';
import ModelConfigurationSelector from './ModelConfigurationSelector';
import TemplateVariableHints from '../TemplateVariableHints';

export default function ExperienceLLMPanel({
  modelConfigurationId,
  promptId,
  inlinePromptTemplate,
  steps,
  includePreviousRun,
  onModelConfigurationChange,
  onFieldChange,
  onInsertVariable,
  validationErrors,
  inlinePromptRef,
}) {
  const promptsQuery = useQuery(
    ['prompts', 'list'],
    async () => {
      const result = await promptAPI.list();
      return result?.data?.items || result?.items || [];
    },
    { staleTime: 30000 }
  );

  const prompts = useMemo(() => {
    const items = promptsQuery.data || [];
    return Array.isArray(items) ? items : [];
  }, [promptsQuery.data]);

  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        Configure the LLM to process step outputs and generate final results. Leave empty if you only want to collect
        data without AI synthesis.
      </Typography>

      <ModelConfigurationSelector
        modelConfigurationId={modelConfigurationId}
        onModelConfigurationChange={onModelConfigurationChange}
        validationErrors={validationErrors}
        required={false}
        showHelperText={true}
        label="Model Configuration"
        showDetails={true}
      />

      {modelConfigurationId && (
        <>
          <Divider />
          <FormControl fullWidth>
            <InputLabel>Prompt Template</InputLabel>
            <Select value={promptId} label="Prompt Template" onChange={onFieldChange('prompt_id')}>
              <MenuItem value="">
                <em>Use model configuration prompt</em>
              </MenuItem>
              {prompts.map((p) => (
                <MenuItem key={p.id} value={p.id}>
                  {p.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          {!promptId && (
            <>
              <TextField
                label="Inline Prompt Template"
                value={inlinePromptTemplate}
                onChange={onFieldChange('inline_prompt_template')}
                fullWidth
                multiline
                rows={20}
                placeholder="Use {{ step_outputs.step_key }} to reference step results"
                helperText="Jinja2 template with access to step_outputs, user, and previous_run. Overrides model configuration prompt."
                inputRef={inlinePromptRef}
              />
              <TemplateVariableHints
                steps={steps}
                includePreviousRun={includePreviousRun}
                onInsert={onInsertVariable}
              />
            </>
          )}
        </>
      )}

      {!modelConfigurationId && (
        <Alert severity="info" sx={{ mt: 1 }}>
          No LLM synthesis configured. This experience will only collect and return step outputs without AI processing.
        </Alert>
      )}
    </Stack>
  );
}
