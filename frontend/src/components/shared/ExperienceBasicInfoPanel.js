import {
  Alert,
  FormControl,
  FormControlLabel,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
} from '@mui/material';

export default function ExperienceBasicInfoPanel({
  name,
  description,
  visibility,
  scope,
  maxRunSeconds,
  includePreviousRun,
  onFieldChange,
  onIncludePreviousRunChange,
  validationErrors = {},
}) {
  return (
    <Stack spacing={2}>
      <TextField
        label="Name"
        value={name}
        onChange={onFieldChange('name')}
        fullWidth
        required
        error={!!validationErrors.name}
        helperText={validationErrors.name}
      />
      <TextField
        label="Description"
        value={description}
        onChange={onFieldChange('description')}
        fullWidth
        multiline
        rows={3}
      />
      <Stack direction="row" spacing={2}>
        <FormControl fullWidth>
          <InputLabel>Visibility</InputLabel>
          <Select value={visibility} label="Visibility" onChange={onFieldChange('visibility')}>
            <MenuItem value="draft">Draft</MenuItem>
            <MenuItem value="admin_only">Admin Only</MenuItem>
            <MenuItem value="published">Published</MenuItem>
          </Select>
          <FormHelperText>
            {visibility === 'published'
              ? 'Visible to all users'
              : visibility === 'admin_only'
                ? 'Only admins can see this'
                : 'Not visible to users yet'}
          </FormHelperText>
        </FormControl>
        <FormControl fullWidth>
          <InputLabel>Scope</InputLabel>
          <Select value={scope} label="Scope" onChange={onFieldChange('scope')}>
            <MenuItem value="user">Per User</MenuItem>
            <MenuItem value="shared">Shared</MenuItem>
          </Select>
          <FormHelperText>
            {scope === 'shared' ? 'Runs once, result shared with all users' : 'Runs once per active user'}
          </FormHelperText>
        </FormControl>
        <TextField
          label="Max Run Time (s)"
          type="number"
          value={maxRunSeconds}
          onChange={onFieldChange('max_run_seconds')}
          fullWidth
          inputProps={{ min: 10, max: 600 }}
          helperText="Timeout before the run is cancelled"
        />
      </Stack>
      {scope === 'shared' && (
        <Alert severity="info">
          This experience will run using the creator's account for data access and plugin authentication.
        </Alert>
      )}
      <FormControlLabel
        control={<Switch checked={includePreviousRun} onChange={onIncludePreviousRunChange} />}
        label="Include output from previous successful run in context"
      />
    </Stack>
  );
}
