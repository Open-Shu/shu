import { Navigate } from 'react-router-dom';
import { useFeatureEnabled } from '../config/featureFlags';

/**
 * FeatureGate — route guard for an entitlement-gated feature.
 *
 * Renders `children` only when the tenant may use `feature` (build-time flag AND
 * entitlement, via useFeatureEnabled); otherwise redirects to /chat, which is
 * always available. Belt-and-braces with the server's 403 and the hidden nav —
 * it covers deep links and stale tabs. Self-hosted deployments report no
 * entitlements, so useFeatureEnabled returns true and children render normally.
 */
export default function FeatureGate({ feature, children }) {
  return useFeatureEnabled(feature) ? children : <Navigate to="/chat" replace />;
}
