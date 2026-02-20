import { useAuth } from '../hooks/useAuth';
import ChangePasswordForm from './ChangePasswordForm';

/**
 * Gate component that blocks app navigation when the user must change their password.
 *
 * Wraps the Router in AuthenticatedApp. When `user.must_change_password` is true,
 * renders a full-page ChangePasswordForm in force mode instead of children.
 * After the user successfully changes their password, the auth context refreshes
 * and normal navigation resumes.
 *
 * A logout link is provided so users are never fully locked out.
 */
const ForceChangePasswordGate = ({ children }) => {
  const { user, logout } = useAuth();

  if (user?.must_change_password === true) {
    return <ChangePasswordForm forceMode onLogout={logout} />;
  }

  return children;
};

export default ForceChangePasswordGate;
