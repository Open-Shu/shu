import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Alert, Box, Button, CircularProgress, Container, Paper, Stack, TextField, Typography } from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import { useAuth } from '../hooks/useAuth';

const RESEND_COOLDOWN_SECONDS = 60;
const EXPIRED_TOKEN_CODE = 'VERIFICATION_TOKEN_EXPIRED';

/**
 * SHU-507 verification landing page.
 *
 * The verification email contains a link of the form
 * `{app_base_url}/verify-email?token=...`. This page reads the token from
 * the query string, POSTs to the backend verify endpoint, and shows one
 * of three terminal states: verified, invalid (or expired), or no token.
 *
 * Recovery paths from a failed verify:
 *
 * - **Expired token (we know who they are)**: simple "Send a new
 *   verification email" button. The same expired token is handed back
 *   to the server, which resolves the user from its hash and issues a
 *   fresh token. The user never has to type, see, or know their email
 *   address.
 * - **Unknown / missing token (we cannot identify the user)**: email
 *   entry form that posts to the email-based resend endpoint.
 */
const VerifyEmailPage = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { verifyEmail, resendVerification, resendVerificationFromToken } = useAuth();

  // 'pending' = network in flight; 'success' / 'invalid' / 'missing' are terminal.
  const [state, setState] = useState('pending');
  const [errorMessage, setErrorMessage] = useState(null);
  // Backend `code` field on the verify error, used to switch between the
  // token-based and email-based recovery flows on the invalid state.
  const [errorCode, setErrorCode] = useState(null);
  const token = searchParams.get('token');

  // Email-based resend state (used on missing-token state and as a
  // fallback on unknown-token state).
  const [resendEmail, setResendEmail] = useState('');
  const [resendNotice, setResendNotice] = useState(null);
  const [resendCooldown, setResendCooldown] = useState(0);

  // Cache the in-flight verify promise per token. React 18 StrictMode
  // runs effects twice in dev, which would otherwise (a) fire the
  // verify endpoint twice — second call always returns "invalid" because
  // the first cleared the token columns — or (b) get stuck on `pending`
  // if we naively gate the second run with a flag.
  //
  // Storing the promise lets BOTH effect runs await the same single
  // request. Whichever effect's cleanup did NOT mark `cancelled` (the
  // second one in StrictMode dev) gets to call setState.
  const verificationPromiseRef = useRef({ token: null, promise: null });

  useEffect(() => {
    if (!token) {
      setState('missing');
      return undefined;
    }

    let cancelled = false;

    let pending = verificationPromiseRef.current;
    if (pending.token !== token) {
      pending = { token, promise: verifyEmail(token) };
      verificationPromiseRef.current = pending;
    }

    pending.promise
      .then(() => {
        if (!cancelled) {
          setState('success');
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setErrorMessage(err.message);
          setErrorCode(err.code || null);
          setState('invalid');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [token, verifyEmail]);

  // Tick the resend cooldown down each second so the button re-enables
  // exactly RESEND_COOLDOWN_SECONDS after the most recent click.
  useEffect(() => {
    if (resendCooldown <= 0) {
      return undefined;
    }
    const id = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000);
    return () => clearTimeout(id);
  }, [resendCooldown]);

  const goToLogin = () => navigate('/auth');

  const handleEmailResend = async (e) => {
    e?.preventDefault?.();
    setResendNotice(null);
    if (!resendEmail) {
      setResendNotice('Enter the email address you registered with.');
      return;
    }
    try {
      const data = await resendVerification(resendEmail);
      setResendNotice(data?.message || 'If an account is pending verification, a new email has been sent.');
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setResendNotice(err.message || 'Could not resend verification email.');
    }
  };

  const handleTokenResend = async () => {
    setResendNotice(null);
    try {
      const data = await resendVerificationFromToken(token);
      setResendNotice(data?.message || 'A new verification email has been sent.');
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setResendNotice(err.message || 'Could not resend verification email.');
    }
  };

  const renderEmailResendForm = () => (
    <Box component="form" onSubmit={handleEmailResend} sx={{ mt: 2 }}>
      <TextField
        label="Email address"
        type="email"
        fullWidth
        value={resendEmail}
        onChange={(event) => setResendEmail(event.target.value)}
        autoComplete="email"
        sx={{ mb: 2 }}
      />
      {resendNotice && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {resendNotice}
        </Alert>
      )}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <Button type="submit" variant="contained" disabled={resendCooldown > 0} fullWidth>
          {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Send a new verification email'}
        </Button>
        <Button variant="outlined" onClick={goToLogin} fullWidth>
          Go to sign in
        </Button>
      </Stack>
    </Box>
  );

  const renderTokenResend = () => (
    <Box sx={{ mt: 2 }}>
      {resendNotice && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {resendNotice}
        </Alert>
      )}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <Button variant="contained" onClick={handleTokenResend} disabled={resendCooldown > 0} fullWidth>
          {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Send a new verification email'}
        </Button>
        <Button variant="outlined" onClick={goToLogin} fullWidth>
          Go to sign in
        </Button>
      </Stack>
    </Box>
  );

  const isExpired = state === 'invalid' && errorCode === EXPIRED_TOKEN_CODE;

  return (
    <Container maxWidth="sm">
      <Box sx={{ marginTop: 8, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <Paper elevation={3} sx={{ padding: 4, width: '100%' }}>
          <Box sx={{ textAlign: 'center' }}>
            {state === 'pending' && (
              <>
                <CircularProgress sx={{ mb: 2 }} />
                <Typography variant="body1" color="text.secondary">
                  Verifying your email…
                </Typography>
              </>
            )}
            {state === 'success' && (
              <>
                <CheckCircleIcon sx={{ fontSize: 56, color: 'success.main', mb: 2 }} />
                <Typography component="h1" variant="h5" gutterBottom>
                  Email verified
                </Typography>
                <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
                  Your account is active. You can now sign in.
                </Typography>
                <Button variant="contained" onClick={goToLogin}>
                  Go to sign in
                </Button>
              </>
            )}
            {state === 'invalid' && isExpired && (
              <>
                <ErrorIcon sx={{ fontSize: 56, color: 'warning.main', mb: 2 }} />
                <Typography component="h1" variant="h5" gutterBottom>
                  Verification link expired
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Click below and we&apos;ll send a fresh verification link to the address on file for this account.
                </Typography>
                {renderTokenResend()}
              </>
            )}
            {state === 'invalid' && !isExpired && (
              <>
                <ErrorIcon sx={{ fontSize: 56, color: 'warning.main', mb: 2 }} />
                <Typography component="h1" variant="h5" gutterBottom>
                  Verification link invalid
                </Typography>
                {errorMessage && (
                  <Alert severity="warning" sx={{ mb: 2 }}>
                    {errorMessage}
                  </Alert>
                )}
                <Typography variant="body2" color="text.secondary">
                  This link is no longer valid. Enter your email and we&apos;ll send a new one.
                </Typography>
                {renderEmailResendForm()}
              </>
            )}
            {state === 'missing' && (
              <>
                <ErrorIcon sx={{ fontSize: 56, color: 'warning.main', mb: 2 }} />
                <Typography component="h1" variant="h5" gutterBottom>
                  No verification token found
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Open the link from your verification email, or request a new one below.
                </Typography>
                {renderEmailResendForm()}
              </>
            )}
          </Box>
        </Paper>
      </Box>
    </Container>
  );
};

export default VerifyEmailPage;
