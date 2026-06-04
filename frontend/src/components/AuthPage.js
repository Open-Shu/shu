import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import GoogleLogin from './GoogleLogin';
import PasswordLogin from './PasswordLogin';
import PasswordRegistration from './PasswordRegistration';
import ForgotPasswordPage from './ForgotPasswordPage';
import configService from '../services/config';

// `initialMode` lets a route open a specific pane (the /register route opens
// 'register'); defaults to the login form. An `email` query param pre-fills the
// registration form — the CP welcome email links to /register?email=<addr> so
// the customer registers with the exact address ADMIN_EMAILS grants admin to.
const AuthPage = ({ initialMode = 'password' }) => {
  const [searchParams] = useSearchParams();
  const prefilledEmail = searchParams.get('email') || '';
  const [authMode, setAuthMode] = useState(initialMode); // 'google', 'password', 'register', 'forgot-password'
  const [googleSsoEnabled, setGoogleSsoEnabled] = useState(false);
  const [microsoftSsoEnabled, setMicrosoftSsoEnabled] = useState(false);

  useEffect(() => {
    let isMounted = true;

    const loadConfig = async () => {
      try {
        await configService.fetchConfig();
        if (!isMounted) {
          return;
        }
        setGoogleSsoEnabled(configService.isGoogleSsoEnabled());
        setMicrosoftSsoEnabled(configService.isMicrosoftSsoEnabled());
      } catch (error) {
        if (!isMounted) {
          return;
        }
        setGoogleSsoEnabled(false);
        setMicrosoftSsoEnabled(false);
      }
    };

    loadConfig();

    return () => {
      isMounted = false;
    };
  }, []);

  const handleSwitchToGoogle = () => {
    if (!googleSsoEnabled) {
      return;
    }
    setAuthMode('google');
  };

  const handleSwitchToPassword = () => {
    setAuthMode('password');
  };

  const handleSwitchToRegister = () => {
    setAuthMode('register');
  };

  const handleSwitchToLogin = () => {
    setAuthMode('password');
  };

  const handleSwitchToForgotPassword = () => {
    setAuthMode('forgot-password');
  };

  switch (authMode) {
    case 'google':
      return googleSsoEnabled ? (
        <GoogleLogin onSwitchToPassword={handleSwitchToPassword} />
      ) : (
        <PasswordLogin
          onSwitchToRegister={handleSwitchToRegister}
          onSwitchToGoogle={handleSwitchToGoogle}
          onSwitchToForgotPassword={handleSwitchToForgotPassword}
          isGoogleSsoEnabled={googleSsoEnabled}
          isMicrosoftSsoEnabled={microsoftSsoEnabled}
        />
      );
    case 'register':
      return <PasswordRegistration onSwitchToLogin={handleSwitchToLogin} initialEmail={prefilledEmail} />;
    case 'forgot-password':
      return <ForgotPasswordPage onSwitchToLogin={handleSwitchToLogin} />;
    case 'password':
    default:
      return (
        <PasswordLogin
          onSwitchToRegister={handleSwitchToRegister}
          onSwitchToGoogle={handleSwitchToGoogle}
          onSwitchToForgotPassword={handleSwitchToForgotPassword}
          isGoogleSsoEnabled={googleSsoEnabled}
          isMicrosoftSsoEnabled={microsoftSsoEnabled}
        />
      );
  }
};

export default AuthPage;
