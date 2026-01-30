import React, { useState, useEffect } from "react";
import GoogleLogin from "./GoogleLogin";
import PasswordLogin from "./PasswordLogin";
import PasswordRegistration from "./PasswordRegistration";
import configService from "../services/config";

const AuthPage = () => {
  const [authMode, setAuthMode] = useState("password"); // 'google', 'password', 'register'
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
    setAuthMode("google");
  };

  const handleSwitchToPassword = () => {
    setAuthMode("password");
  };

  const handleSwitchToRegister = () => {
    setAuthMode("register");
  };

  const handleSwitchToLogin = () => {
    setAuthMode("password");
  };

  switch (authMode) {
    case "google":
      return googleSsoEnabled ? (
        <GoogleLogin onSwitchToPassword={handleSwitchToPassword} />
      ) : (
        <PasswordLogin
          onSwitchToRegister={handleSwitchToRegister}
          onSwitchToGoogle={handleSwitchToGoogle}
          isGoogleSsoEnabled={googleSsoEnabled}
          isMicrosoftSsoEnabled={microsoftSsoEnabled}
        />
      );
    case "register":
      return <PasswordRegistration onSwitchToLogin={handleSwitchToLogin} />;
    case "password":
    default:
      return (
        <PasswordLogin
          onSwitchToRegister={handleSwitchToRegister}
          onSwitchToGoogle={handleSwitchToGoogle}
          isGoogleSsoEnabled={googleSsoEnabled}
          isMicrosoftSsoEnabled={microsoftSsoEnabled}
        />
      );
  }
};

export default AuthPage;
