import React from 'react';
import { Alert, Box } from '@mui/material';
import { useAuth } from '../hooks/useAuth';

const ProtectedRoute = ({ children, requiredRole = null, requireAuth = true, fallback = null }) => {
  const { isAuthenticated, hasRole, loading } = useAuth();

  // Show loading state while checking authentication
  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
        Loading...
      </Box>
    );
  }

  // Check authentication requirement
  if (requireAuth && !isAuthenticated) {
    return fallback || <Alert severity="error">You must be logged in to access this page.</Alert>;
  }

  // Check role requirement
  if (requiredRole && !hasRole(requiredRole)) {
    return (
      fallback || (
        <Alert severity="error">You don't have permission to access this page. Required role: {requiredRole}</Alert>
      )
    );
  }

  return children;
};

// Higher-order component for protecting routes
export const withAuth = (Component, requiredRole = null) => {
  const WithAuthComponent = (props) => (
    <ProtectedRoute requiredRole={requiredRole}>
      <Component {...props} />
    </ProtectedRoute>
  );
  WithAuthComponent.displayName = `withAuth(${Component.displayName || Component.name || 'Component'})`;
  return WithAuthComponent;
};

// Component for conditionally rendering content based on roles
export const RoleGuard = ({ children, requiredRole, fallback = null, inverse = false }) => {
  const { hasRole } = useAuth();

  const hasPermission = hasRole(requiredRole);
  const shouldShow = inverse ? !hasPermission : hasPermission;

  return shouldShow ? children : fallback;
};

export default ProtectedRoute;
