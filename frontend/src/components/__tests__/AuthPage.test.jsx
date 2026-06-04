/* eslint-disable react/display-name -- jest.mock factory stubs need no display name */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AuthPage from '../AuthPage';

// Isolate AuthPage's routing/prefill logic from the child panes' own plumbing
// (useAuth, api, MUI). Each stub surfaces the props AuthPage passes it.
jest.mock('../PasswordRegistration', () => (props) => (
  <div data-testid="register-form" data-initial-email={props.initialEmail || ''} />
));
jest.mock('../PasswordLogin', () => () => <div data-testid="login-form" />);
jest.mock('../GoogleLogin', () => () => <div data-testid="google-login" />);
jest.mock('../ForgotPasswordPage', () => () => <div data-testid="forgot-form" />);
jest.mock('../../services/config', () => ({
  __esModule: true,
  default: {
    fetchConfig: jest.fn().mockResolvedValue(undefined),
    isGoogleSsoEnabled: () => false,
    isMicrosoftSsoEnabled: () => false,
  },
}));

const renderAt = (path, props = {}) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthPage {...props} />
    </MemoryRouter>
  );

describe('AuthPage register routing + email prefill', () => {
  it('initialMode="register" renders the registration form, not login', async () => {
    renderAt('/register', { initialMode: 'register' });
    expect(await screen.findByTestId('register-form')).not.toBeNull();
    expect(screen.queryByTestId('login-form')).toBeNull();
  });

  it('prefills the registration email from ?email=', async () => {
    renderAt('/register?email=foo%40bar.com', { initialMode: 'register' });
    const form = await screen.findByTestId('register-form');
    expect(form.getAttribute('data-initial-email')).toBe('foo@bar.com');
  });

  it('decodes reserved characters (e.g. +) in the email param', async () => {
    renderAt('/register?email=owner%2Bshu%40acme.com', { initialMode: 'register' });
    const form = await screen.findByTestId('register-form');
    expect(form.getAttribute('data-initial-email')).toBe('owner+shu@acme.com');
  });

  it('defaults to the login form when no initialMode is given', async () => {
    renderAt('/auth');
    expect(await screen.findByTestId('login-form')).not.toBeNull();
    expect(screen.queryByTestId('register-form')).toBeNull();
  });
});
