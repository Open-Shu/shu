import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from 'react-query';
import { ThemeProvider as MuiThemeProvider } from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
import { Box } from '@mui/material';

// Admin Components
import QuickStart from './components/QuickStart';
import KnowledgeBases from './components/KnowledgeBases';
import Documents from './components/Documents';

import QueryTester from './components/QueryTester';
import LLMTester from './components/LLMTester';
import HealthMonitor from './components/HealthMonitor';
import UserManagement from './components/UserManagement';
import UserGroups from './components/UserGroups';
import KBPermissions from './components/KBPermissions';
import LLMProviders from './components/LLMProviders';
import ModelConfigurations from './components/ModelConfigurations';
import Prompts from './pages/Prompts';
import PluginsAdmin from './components/PluginsAdmin';
import PluginsAdminFeeds from './components/PluginsAdminFeeds';
import ExperiencesAdmin from './components/ExperiencesAdmin';
import ExperienceEditor from './components/ExperienceEditor';
import BrandingSettings from './components/admin/BrandingSettings';
import DashboardPage from './pages/DashboardPage';
import ExperienceDetailPage from './pages/ExperienceDetailPage';

// User Components
import ModernChat from './components/ModernChat';
import UserPermissionsDashboard from './components/UserPermissionsDashboard';
import ConnectedAccountsPage from './components/ConnectedAccountsPage';
import UserPreferencesPage from './components/UserPreferencesPage';

// Auth Components
import AuthPage from './components/AuthPage';
import ProtectedRoute from './components/ProtectedRoute';
import RoleBasedRoute from './components/RoleBasedRoute';
import { AuthProvider, useAuth } from './hooks/useAuth';

// Theme Context
import { ThemeProvider as CustomThemeProvider, useTheme } from './contexts/ThemeContext';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        // Don't retry on authentication errors
        if (error?.response?.status === 401) {
          return false;
        }
        // Retry once for other errors
        return failureCount < 1;
      },
      refetchOnWindowFocus: false,
    },
  },
});

const pageLoadingView = (
  <Box display="flex" justifyContent="center" alignItems="center" minHeight="100vh">
    Loading...
  </Box>
);

// Auth page wrapper - redirect if already authenticated
const AuthPageWrapper = () => {
  const { user, loading: authLoading } = useAuth();

  if (authLoading) {
    return pageLoadingView;
  }

  // If user is already authenticated, redirect to chat
  if (user) {
    return <Navigate to="/chat" replace />;
  }

  // Otherwise show the auth page
  return <AuthPage />;
};

// Main app redirect - everyone goes to chat by default
const MainAppRedirect = () => {
  const { user, loading: authLoading } = useAuth();

  if (authLoading) {
    return pageLoadingView;
  }

  if (!user) {
    return <Navigate to="/auth" replace />;
  }

  // Everyone goes to the main chat interface by default
  return <Navigate to="/chat" replace />;
};

// Authenticated app component
const AuthenticatedApp = () => {
  const { isAuthenticated, loading: authLoading } = useAuth();

  if (authLoading) {
    return pageLoadingView;
  }

  if (!isAuthenticated) {
    return <AuthPage />;
  }

  return (
    <Router>
      <Routes>
        {/* Root redirect - everyone goes to chat */}
        <Route path="/" element={<MainAppRedirect />} />

        {/* Auth route - redirect if already authenticated */}
        <Route path="/auth" element={<AuthPageWrapper />} />

        {/* Main Chat Interface - Available to ALL users */}
        <Route
          path="/chat"
          element={
            <RoleBasedRoute layout="user">
              <ModernChat />
            </RoleBasedRoute>
          }
        />

        {/* Experience Dashboard - Available to ALL users */}
        <Route
          path="/dashboard"
          element={
            <RoleBasedRoute layout="user">
              <DashboardPage />
            </RoleBasedRoute>
          }
        />
        <Route
          path="/dashboard/experience/:experienceId"
          element={
            <RoleBasedRoute layout="user">
              <ExperienceDetailPage />
            </RoleBasedRoute>
          }
        />

        {/* User Permissions Dashboard - Available to ALL users */}
        <Route
          path="/permissions"
          element={
            <RoleBasedRoute layout="user">
              <UserPermissionsDashboard />
            </RoleBasedRoute>
          }
        />

        {/* Connected Accounts - Available to ALL users */}
        <Route
          path="/settings/connected-accounts"
          element={
            <RoleBasedRoute layout="user">
              <ConnectedAccountsPage />
            </RoleBasedRoute>
          }
        />

        {/* User Preferences - Available to ALL users */}
        <Route path="/settings/preferences" element={<Navigate to="/settings/preferences/general" replace />} />
        <Route
          path="/settings/preferences/:section"
          element={
            <RoleBasedRoute layout="user">
              <UserPreferencesPage />
            </RoleBasedRoute>
          }
        />

        {/* Admin Routes */}
        <Route
          path="/admin/dashboard"
          element={
            <RoleBasedRoute adminOnly>
              <QuickStart />
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/knowledge-bases"
          element={
            <RoleBasedRoute adminOnly>
              <KnowledgeBases />
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/knowledge-bases/:kbId/documents"
          element={
            <RoleBasedRoute adminOnly>
              <Documents />
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/prompts"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="power_user">
                <Prompts />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />

        <Route
          path="/admin/query-tester"
          element={
            <RoleBasedRoute adminOnly>
              <QueryTester />
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/llm-tester"
          element={
            <RoleBasedRoute adminOnly>
              <LLMTester />
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/health"
          element={
            <RoleBasedRoute adminOnly>
              <HealthMonitor />
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/llm-providers"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="admin">
                <LLMProviders />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/model-configurations"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="power_user">
                <ModelConfigurations />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/branding"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="admin">
                <BrandingSettings />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/users"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="admin">
                <UserManagement />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/user-groups"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="admin">
                <UserGroups />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/kb-permissions"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="admin">
                <KBPermissions />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/plugins"
          element={
            <RoleBasedRoute adminOnly>
              <PluginsAdmin />
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/feeds"
          element={
            <RoleBasedRoute adminOnly>
              <PluginsAdminFeeds />
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/experiences"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="admin">
                <ExperiencesAdmin />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/experiences/new"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="admin">
                <ExperienceEditor />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />
        <Route
          path="/admin/experiences/:experienceId/edit"
          element={
            <RoleBasedRoute adminOnly>
              <ProtectedRoute requiredRole="admin">
                <ExperienceEditor />
              </ProtectedRoute>
            </RoleBasedRoute>
          }
        />

        {/* Catch all - redirect to main chat interface */}
        <Route path="*" element={<MainAppRedirect />} />
      </Routes>
    </Router>
  );
};

// Wrapper component to access theme from context
const ThemedApp = () => {
  const { theme } = useTheme();

  return (
    <MuiThemeProvider theme={theme}>
      <CssBaseline />
      <AuthenticatedApp />
    </MuiThemeProvider>
  );
};

// Main app component
function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <CustomThemeProvider>
          <ThemedApp />
        </CustomThemeProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
