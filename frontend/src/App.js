import React, { useEffect } from 'react';
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
import LLMProviders from './components/LLMProviders';
import ModelConfigurations from './components/ModelConfigurations';
import Prompts from './pages/Prompts';
import McpAdmin from './components/McpAdmin';
import PluginsAdmin from './components/PluginsAdmin';
import PluginsAdminFeeds from './components/PluginsAdminFeeds';
import ExperiencesAdmin from './components/ExperiencesAdmin';
import PolicyAdmin from './components/PolicyAdmin';
import ExperienceEditor from './components/ExperienceEditor';
import BrandingSettings from './components/admin/BrandingSettings';
import DashboardPage from './pages/DashboardPage';
import ExperienceDetailPage from './pages/ExperienceDetailPage';
import CostUsagePage from './pages/CostUsagePage';

// User Components
import ModernChat from './components/ModernChat';
import UserPermissionsDashboard from './components/UserPermissionsDashboard';
import ConnectedAccountsPage from './components/ConnectedAccountsPage';
import UserPreferencesPage from './components/UserPreferencesPage';

// Auth Components
import AuthPage from './components/AuthPage';
import VerifyEmailPage from './components/VerifyEmailPage';
import ResetPasswordPage from './components/ResetPasswordPage';
import ProtectedRoute from './components/ProtectedRoute';
import RoleBasedRoute from './components/RoleBasedRoute';
import ForceChangePasswordGate from './components/ForceChangePasswordGate';
import { AuthProvider, useAuth } from './hooks/useAuth';

import configService from './services/config';
import { PLUGINS_ENABLED, MCP_ENABLED, EXPERIENCES_ENABLED } from './config/featureFlags';

// Theme Context
import { ThemeProvider as CustomThemeProvider, useTheme } from './contexts/ThemeContext';
import { BillingStatusProvider } from './contexts/BillingStatusContext';
import PaymentBanner from './components/PaymentBanner';
import TrialBanner from './components/TrialBanner';

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

  // Fetch public config on mount so it's available regardless of auth state.
  // AuthPage also calls this, but authenticated users skip AuthPage entirely.
  useEffect(() => {
    configService.fetchConfig();
  }, []);

  if (authLoading) {
    return pageLoadingView;
  }

  // Unauthenticated users still need access to a small set of public
  // routes (most importantly the SHU-507 email-verification landing
  // page — the token in the URL is the auth credential, so by definition
  // the visitor is not logged in yet). Render a minimal Router for the
  // unauth path; everything else falls through to <AuthPage />.
  if (!isAuthenticated) {
    return (
      <Router>
        <Routes>
          <Route path="/verify-email" element={<VerifyEmailPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="*" element={<AuthPage />} />
        </Routes>
      </Router>
    );
  }

  return (
    // Provider lives inside the auth boundary so polling /billing/subscription only fires for logged-in users.
    // The flex-column wrapper claims the viewport so the in-flow PaymentBanner
    // pushes the routed layout down instead of overlapping it. Inner layouts
    // (UserLayout / AdminLayout) must use `height: '100%'`, not `100vh`, so
    // they fit inside the remaining space.
    <BillingStatusProvider>
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
        <TrialBanner />
        <PaymentBanner />
        <Box sx={{ flexGrow: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <ForceChangePasswordGate>
            <Router>
              <Routes>
                {/* Root redirect - everyone goes to chat */}
                <Route path="/" element={<MainAppRedirect />} />

                {/* Auth route - redirect if already authenticated */}
                <Route path="/auth" element={<AuthPageWrapper />} />

                {/* SHU-507 email-verification landing page is also reachable
                    when authenticated — e.g. an admin clicking a verification
                    link for another account. The verify endpoint matches by
                    token hash, so the current session is unaffected. */}
                <Route path="/verify-email" element={<VerifyEmailPage />} />

                {/* SHU-745 password-reset page — also reachable when
                    authenticated; the same token-hash lookup applies, and the
                    current session is unaffected unless the user resets their
                    own password (in which case the iat-vs-password_changed_at
                    gate logs them out on next request, as designed). */}
                <Route path="/reset-password" element={<ResetPasswordPage />} />

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
                {EXPERIENCES_ENABLED && (
                  <Route
                    path="/dashboard"
                    element={
                      <RoleBasedRoute layout="user">
                        <DashboardPage />
                      </RoleBasedRoute>
                    }
                  />
                )}
                {EXPERIENCES_ENABLED && (
                  <Route
                    path="/dashboard/experience/:experienceId"
                    element={
                      <RoleBasedRoute layout="user">
                        <ExperienceDetailPage />
                      </RoleBasedRoute>
                    }
                  />
                )}

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
                {PLUGINS_ENABLED && (
                  <Route
                    path="/settings/connected-accounts"
                    element={
                      <RoleBasedRoute layout="user">
                        <ConnectedAccountsPage />
                      </RoleBasedRoute>
                    }
                  />
                )}

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
                  path="/admin/billing/usage"
                  element={
                    <RoleBasedRoute adminOnly>
                      <ProtectedRoute requiredRole="admin">
                        <CostUsagePage />
                      </ProtectedRoute>
                    </RoleBasedRoute>
                  }
                />
                {PLUGINS_ENABLED && (
                  <Route
                    path="/admin/plugins"
                    element={
                      <RoleBasedRoute adminOnly>
                        <PluginsAdmin />
                      </RoleBasedRoute>
                    }
                  />
                )}
                {MCP_ENABLED && (
                  <Route
                    path="/admin/mcp"
                    element={
                      <RoleBasedRoute adminOnly>
                        <McpAdmin />
                      </RoleBasedRoute>
                    }
                  />
                )}
                {PLUGINS_ENABLED && (
                  <Route
                    path="/admin/feeds"
                    element={
                      <RoleBasedRoute adminOnly>
                        <PluginsAdminFeeds />
                      </RoleBasedRoute>
                    }
                  />
                )}
                {EXPERIENCES_ENABLED && (
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
                )}
                {EXPERIENCES_ENABLED && (
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
                )}
                {EXPERIENCES_ENABLED && (
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
                )}
                <Route
                  path="/admin/policies"
                  element={
                    <RoleBasedRoute adminOnly>
                      <ProtectedRoute requiredRole="admin">
                        <PolicyAdmin />
                      </ProtectedRoute>
                    </RoleBasedRoute>
                  }
                />

                {/* Catch all - redirect to main chat interface */}
                <Route path="*" element={<MainAppRedirect />} />
              </Routes>
            </Router>
          </ForceChangePasswordGate>
        </Box>
      </Box>
    </BillingStatusProvider>
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
