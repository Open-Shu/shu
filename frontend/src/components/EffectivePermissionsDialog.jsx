import { useQuery } from 'react-query';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Alert,
  Typography,
  Box,
  Chip,
  CircularProgress,
  Divider,
  Link,
} from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import { policyAPI, extractDataFromResponse, formatError } from '../services/api';
import { resolveUserId } from '../utils/userHelpers';

const StatementList = ({ statements }) => (
  <Box sx={{ pl: 2, pt: 0.5 }}>
    {statements.map((stmt, i) => (
      <Box key={i} sx={{ display: 'flex', gap: 3, mb: 1 }}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: 'block' }}>
            Actions
          </Typography>
          {(stmt.actions || []).map((action) => (
            <Chip key={action} label={action} size="small" variant="outlined" sx={{ display: 'flex', mb: 0.5 }} />
          ))}
        </Box>
        <Box sx={{ flex: 1 }}>
          <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: 'block' }}>
            Resources
          </Typography>
          {(stmt.resources || []).map((resource) => (
            <Chip key={resource} label={resource} size="small" variant="outlined" sx={{ display: 'flex', mb: 0.5 }} />
          ))}
        </Box>
      </Box>
    ))}
  </Box>
);

const BindingSummary = ({ bindings }) => {
  const users = bindings.filter((b) => b.actor_type === 'user').length;
  const groups = bindings.filter((b) => b.actor_type === 'group').length;
  if (!users && !groups) {
    return null;
  }
  const parts = [];
  if (users) {
    parts.push(`${users} user${users !== 1 ? 's' : ''}`);
  }
  if (groups) {
    parts.push(`${groups} group${groups !== 1 ? 's' : ''}`);
  }
  return (
    <Typography variant="caption" color="text.secondary">
      Bound to: {parts.join(', ')}
    </Typography>
  );
};

const PolicyCard = ({ policy, onClose }) => (
  <Box sx={{ mb: 2 }}>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
      <Link
        component={RouterLink}
        to={`/admin/policies?policyId=${policy.id}`}
        variant="subtitle2"
        underline="hover"
        onClick={onClose}
      >
        {policy.name}
      </Link>
      <Chip label={policy.effect} size="small" color={policy.effect === 'allow' ? 'success' : 'error'} />
    </Box>
    {policy.description && (
      <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
        {policy.description}
      </Typography>
    )}
    {policy.bindings?.length > 0 && <BindingSummary bindings={policy.bindings} />}
    {policy.statements?.length > 0 && <StatementList statements={policy.statements} />}
  </Box>
);

const EffectivePermissionsDialog = ({ open, onClose, user }) => {
  const userId = resolveUserId(user);

  const { data, isLoading, error } = useQuery(
    ['effectivePolicies', userId],
    () => policyAPI.effective(userId).then(extractDataFromResponse),
    { enabled: !!userId && open }
  );

  const policies = [...(data?.policies || [])].sort((a, b) => {
    if (a.effect === 'allow' && b.effect !== 'allow') {
      return -1;
    }
    if (a.effect !== 'allow' && b.effect === 'allow') {
      return 1;
    }
    return 0;
  });

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Effective Permissions — {user?.name}</DialogTitle>
      <DialogContent dividers>
        {isLoading && (
          <Box display="flex" justifyContent="center" p={3}>
            <CircularProgress />
          </Box>
        )}

        {error && <Alert severity="error">{formatError(error)?.message || formatError(error)}</Alert>}

        {!isLoading && !error && policies.length === 0 && (
          <Typography color="text.secondary">No policies apply to this user.</Typography>
        )}

        {!isLoading &&
          policies.map((policy, i) => (
            <Box key={policy.id}>
              {i > 0 && <Divider sx={{ my: 1 }} />}
              <PolicyCard policy={policy} onClose={onClose} />
            </Box>
          ))}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} variant="contained">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default EffectivePermissionsDialog;
