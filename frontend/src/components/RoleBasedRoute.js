import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import AdminLayout from '../layouts/AdminLayout';
import UserLayout from '../layouts/UserLayout';

/**
 * Route component that renders different layouts based on user role
 * and controls access to admin-only features
 */
const RoleBasedRoute = ({ children, adminOnly = false, layout = 'auto' }) => {
  const { user, loading } = useAuth();

  // Show loading while auth is being determined
  if (loading) {
    return <div>Loading...</div>;
  }

  // Redirect to login if not authenticated
  if (!user) {
    return <Navigate to="/auth" replace />;
  }

  const isAdmin = user.role === 'admin';
  const isPowerUser = user.role === 'power_user';

  // Admin-only routes - check permissions
  if (adminOnly) {
    if (!isAdmin && !isPowerUser) {
      // Redirect non-admin users to main chat interface
      return <Navigate to="/chat" replace />;
    }
    // Admin routes always use AdminLayout
    return <AdminLayout>{children}</AdminLayout>;
  }

  // For non-admin routes, use specified layout or auto-detect
  if (layout === 'admin') {
    return <AdminLayout>{children}</AdminLayout>;
  } else if (layout === 'user') {
    return <UserLayout>{children}</UserLayout>;
  } else {
    // Auto-detect: Main chat interface always uses UserLayout for everyone
    // This ensures a clean, focused chat experience for all users
    return <UserLayout>{children}</UserLayout>;
  }
};

export default RoleBasedRoute;
