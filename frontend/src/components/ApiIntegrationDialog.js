import React, { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useMutation, useQueryClient } from 'react-query';
import yaml from 'js-yaml';
import { extractDataFromResponse, formatError } from '../services/api';
import { apiIntegrationsAPI } from '../services/apiIntegrationsApi';

export default function ApiIntegrationDialog({ open, onClose }) {
  const qc = useQueryClient();
  const [yamlContent, setYamlContent] = useState('');
  const [authCredential, setAuthCredential] = useState('');
  const [hasAuth, setHasAuth] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      setYamlContent('');
      setAuthCredential('');
      setHasAuth(false);
      setError(null);
    }
  }, [open]);

  useEffect(() => {
    if (!yamlContent.trim()) {
      setHasAuth(false);
      return;
    }
    try {
      const parsed = yaml.load(yamlContent);
      setHasAuth(!!(parsed && parsed.auth));
    } catch {
      setHasAuth(false);
    }
  }, [yamlContent]);

  const createMut = useMutation(
    ({ yamlContent: yc, authCredential: ac }) =>
      apiIntegrationsAPI.createConnection(yc, ac).then(extractDataFromResponse),
    {
      onSuccess: () => {
        qc.invalidateQueries(['api-integrations', 'connections']);
        onClose();
      },
      onError: (err) => setError(formatError(err)),
    }
  );

  const isSaving = createMut.isLoading;

  const handleSubmit = () => {
    setError(null);
    if (!yamlContent.trim()) {
      setError('YAML content is required');
      return;
    }
    try {
      yaml.load(yamlContent);
    } catch (e) {
      setError(`Invalid YAML: ${e.message}`);
      return;
    }
    createMut.mutate({ yamlContent, authCredential: authCredential || undefined });
  };

  return (
    <Dialog open={open} onClose={isSaving ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>Add API Integration</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}

          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              YAML Configuration
            </Typography>
            <TextField
              value={yamlContent}
              onChange={(e) => setYamlContent(e.target.value)}
              fullWidth
              required
              multiline
              minRows={10}
              maxRows={20}
              placeholder={`name: my-api\nbase_url: https://api.example.com\nauth:\n  type: bearer\nendpoints:\n  - name: list_items\n    path: /items\n    method: GET`}
              inputProps={{ 'data-testid': 'yaml-input', style: { fontFamily: 'monospace', fontSize: '0.875rem' } }}
              helperText="Paste or type your API integration YAML configuration"
            />
          </Box>

          {hasAuth && (
            <TextField
              label="Auth Credential"
              value={authCredential}
              onChange={(e) => setAuthCredential(e.target.value)}
              fullWidth
              type="password"
              placeholder="API key or token"
              helperText="Credential for the auth section defined in your YAML (stored encrypted)"
              inputProps={{ 'data-testid': 'auth-input' }}
            />
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <Button onClick={handleSubmit} variant="contained" disabled={isSaving}>
          {isSaving ? 'Creating...' : 'Add Integration'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
