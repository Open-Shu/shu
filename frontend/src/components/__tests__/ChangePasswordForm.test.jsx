import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi } from 'vitest';
import ChangePasswordForm from '../ChangePasswordForm';
import { useAuth } from '../../hooks/useAuth';
import { authAPI, formatError } from '../../services/api';

vi.mock('../../hooks/useAuth', () => ({
  useAuth: vi.fn(),
}));

vi.mock('../../services/api', () => ({
  authAPI: {
    changePassword: vi.fn(),
  },
  formatError: vi.fn((err) => err.message || 'Something went wrong'),
}));

vi.mock('../../services/config', () => ({
  default: {
    getPasswordPolicy: vi.fn(() => ({
      policy: 'moderate',
      min_length: 8,
      special_chars: '!@#$%^&*()-_+=',
    })),
  },
}));

const passwordUser = {
  user: { auth_method: 'password', name: 'Test User', email: 'test@example.com' },
  refreshUser: vi.fn(),
};

describe('ChangePasswordForm', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue(passwordUser);
  });

  test('renders form with 3 password fields for password-auth users', () => {
    render(<ChangePasswordForm />);

    expect(screen.getByLabelText(/^current password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^new password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^confirm new password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /change password/i })).toBeInTheDocument();
  });

  test('shows info Alert for SSO users', () => {
    useAuth.mockReturnValue({
      user: { auth_method: 'google', name: 'SSO User', email: 'sso@example.com' },
      refreshUser: vi.fn(),
    });

    render(<ChangePasswordForm />);

    expect(screen.getByText(/password change is not available for google sso accounts/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/current password/i)).not.toBeInTheDocument();
  });

  test('validates minimum length and shows requirements checklist', () => {
    render(<ChangePasswordForm />);

    const newPasswordField = screen.getByLabelText(/^new password/i);
    fireEvent.change(newPasswordField, { target: { value: 'Ab1' } });

    // Requirements checklist should appear once new password has content
    expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument();
    expect(screen.getByText(/at least one uppercase letter/i)).toBeInTheDocument();
    expect(screen.getByText(/at least one lowercase letter/i)).toBeInTheDocument();
    expect(screen.getByText(/at least one digit/i)).toBeInTheDocument();
  });

  test('shows "Passwords do not match" when confirm differs', () => {
    render(<ChangePasswordForm />);

    const newPasswordField = screen.getByLabelText(/^new password/i);
    const confirmPasswordField = screen.getByLabelText(/confirm new password/i);

    fireEvent.change(newPasswordField, { target: { value: 'NewPass123' } });
    fireEvent.change(confirmPasswordField, { target: { value: 'Different1' } });

    expect(screen.getByText('Passwords do not match')).toBeInTheDocument();
  });

  test('shows same-as-current error when new password matches current', () => {
    render(<ChangePasswordForm />);

    const currentPasswordField = screen.getByLabelText(/current password/i);
    const newPasswordField = screen.getByLabelText(/^new password/i);

    fireEvent.change(currentPasswordField, { target: { value: 'SamePass123' } });
    fireEvent.change(newPasswordField, { target: { value: 'SamePass123' } });

    expect(screen.getByText(/new password must be different from current password/i)).toBeInTheDocument();
  });

  test('successful submit shows success alert and clears form', async () => {
    authAPI.changePassword.mockResolvedValue({ data: { message: 'Password changed' } });

    render(<ChangePasswordForm />);

    const currentPasswordField = screen.getByLabelText(/current password/i);
    const newPasswordField = screen.getByLabelText(/^new password/i);
    const confirmPasswordField = screen.getByLabelText(/confirm new password/i);

    fireEvent.change(currentPasswordField, { target: { value: 'OldPassword1' } });
    fireEvent.change(newPasswordField, { target: { value: 'NewPassword1' } });
    fireEvent.change(confirmPasswordField, { target: { value: 'NewPassword1' } });

    const submitButton = screen.getByRole('button', { name: /change password/i });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText('Password changed successfully.')).toBeInTheDocument();
    });

    // Fields should be cleared
    expect(currentPasswordField.value).toBe('');
    expect(newPasswordField.value).toBe('');
    expect(confirmPasswordField.value).toBe('');
  });

  test('API error shows error alert', async () => {
    const apiError = new Error('Current password is incorrect');
    authAPI.changePassword.mockRejectedValue(apiError);
    formatError.mockReturnValue('Current password is incorrect');

    render(<ChangePasswordForm />);

    const currentPasswordField = screen.getByLabelText(/current password/i);
    const newPasswordField = screen.getByLabelText(/^new password/i);
    const confirmPasswordField = screen.getByLabelText(/confirm new password/i);

    fireEvent.change(currentPasswordField, { target: { value: 'WrongPassword1' } });
    fireEvent.change(newPasswordField, { target: { value: 'NewPassword1' } });
    fireEvent.change(confirmPasswordField, { target: { value: 'NewPassword1' } });

    const submitButton = screen.getByRole('button', { name: /change password/i });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText('Current password is incorrect')).toBeInTheDocument();
    });

    expect(formatError).toHaveBeenCalledWith(apiError);
  });

  test('forceMode renders explanation text', () => {
    render(<ChangePasswordForm forceMode />);

    expect(screen.getByText('Password Change Required')).toBeInTheDocument();
    expect(screen.getByText(/your administrator has reset your password/i)).toBeInTheDocument();
  });
});
