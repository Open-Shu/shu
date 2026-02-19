import React from 'react';

import { useAuth } from '../hooks/useAuth';
import ChangePasswordForm from './ChangePasswordForm';

/**
 * Gate component that blocks app navigation when the user must change their password.
 *
 * Wraps the Router in AuthenticatedApp. When `user.must_change_password` is true,
 * renders a full-page ChangePasswordForm in force mode instead of children.
 * After the user successfully changes their password, the auth context refreshes
 * and normal navigation resumes.
 */
const ForceChangePasswordGate = ({ children }) => {
  const { user, refreshUser } = useAuth();

  if (user?.must_change_password === true) {
    const handleSuccess = async () => {
      await refreshUser();
    };

    return <ChangePasswordForm forceMode onSuccess={handleSuccess} />;
  }

  return children;
};

export default ForceChangePasswordGate;
