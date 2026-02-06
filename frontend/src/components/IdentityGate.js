import React from 'react';
import { Box, Typography } from '@mui/material';
import IdentityStatus from './IdentityStatus';

/**
 * IdentityGate
 * Renders Required Identities connection UI and reports readiness via onStatusChange.
 * Use this anywhere a tool/plugin execution requires user/service identities.
 */
export default function IdentityGate({
  requiredIdentities = [],
  onStatusChange,
  title = 'Required Identities',
  identityStatusProps = {},
}) {
  if (!Array.isArray(requiredIdentities) || requiredIdentities.length === 0) {
    return null;
  }
  return (
    <Box mb={2}>
      {title ? (
        <Typography variant="subtitle1" gutterBottom>
          {title}
        </Typography>
      ) : null}
      <IdentityStatus
        requiredIdentities={requiredIdentities}
        onStatusChange={(ok) => {
          if (typeof onStatusChange === 'function') {
            onStatusChange(!!ok);
          }
        }}
        {...identityStatusProps}
      />
    </Box>
  );
}

/** Helper: returns true if actions should be disabled until identities are connected */
export function identityGateDisabled(requiredIdentities, identitiesOk) {
  return Array.isArray(requiredIdentities) && requiredIdentities.length > 0 && !identitiesOk;
}
