/**
 * Tests for ContactSupportDialog (SHU-857).
 *
 * Asserts the load-bearing behavior: the support address renders, Copy hits
 * the Clipboard API, "Email us" builds a correct mailto href with prefilled
 * subject/body, the version shows inline, and the Shu Bot stub navigates to
 * /chat (and closes the dialog).
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { MemoryRouter } from 'react-router-dom';

import ContactSupportDialog from '../ContactSupportDialog';

// `mock`-prefixed so Vitest allows referencing it inside the hoisted vi.mock factory.
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// Build-time flag gating the Shu Assistant section. A getter lets each test
// toggle it via the `mock`-prefixed holder before rendering.
let mockAssistantEnabled = true;
vi.mock('../../../config/featureFlags', () => ({
  get SHU_ASSISTANT_ENABLED() {
    return mockAssistantEnabled;
  },
}));

const USER = { name: 'Eric Longville', email: 'eric@example.com', role: 'admin' };

const renderDialog = (props = {}) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <MemoryRouter>
        <ContactSupportDialog
          open
          onClose={props.onClose || vi.fn()}
          user={USER}
          appName="Shu"
          version="v1.2.3 • abc1234"
          {...props}
        />
      </MemoryRouter>
    </ThemeProvider>
  );

// Capture the original clipboard so the tests that stub it (Copy / unavailable
// cases) don't leak a mutated global to anything added later in this file.
const originalClipboard = navigator.clipboard;

beforeEach(() => {
  mockNavigate.mockClear();
  mockAssistantEnabled = true;
});

afterEach(() => {
  Object.assign(navigator, { clipboard: originalClipboard });
});

describe('ContactSupportDialog', () => {
  it('renders the support address and version inline', () => {
    renderDialog();
    expect(screen.getByText('support@shu.ai')).toBeInTheDocument();
    expect(screen.getByText('v1.2.3 • abc1234')).toBeInTheDocument();
  });

  it('builds an "Email us" mailto with prefilled subject and account context', () => {
    renderDialog();
    const link = screen.getByRole('link', { name: /email us/i });
    const href = link.getAttribute('href');
    expect(href).toMatch(/^mailto:support@shu\.ai\?/);

    const params = new URLSearchParams(new URL(href).search);
    expect(params.get('subject')).toBe('Shu Support Request');
    const body = params.get('body');
    expect(body).toContain('App: Shu v1.2.3 • abc1234');
    expect(body).toContain('Name: Eric Longville');
    expect(body).toContain('Email: eric@example.com');
    expect(body).toContain('Role: admin');
  });

  it('copies the address to the clipboard when Copy is clicked', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: /copy support email address/i }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('support@shu.ai'));
  });

  it('does not throw when the Clipboard API is unavailable', () => {
    Object.assign(navigator, { clipboard: undefined });
    renderDialog();
    // Should be a no-op, not a thrown error.
    expect(() => fireEvent.click(screen.getByRole('button', { name: /copy support email address/i }))).not.toThrow();
  });

  it('navigates to /chat and closes when "Chat with Shu Assistant" is clicked', () => {
    const onClose = vi.fn();
    renderDialog({ onClose });

    fireEvent.click(screen.getByRole('button', { name: /chat with shu assistant/i }));

    expect(mockNavigate).toHaveBeenCalledWith('/chat');
    expect(onClose).toHaveBeenCalled();
  });

  it('hides the Shu Assistant section when the flag is disabled', () => {
    mockAssistantEnabled = false;
    renderDialog();

    // Support contact path remains; the assistant entry is gone.
    expect(screen.getByText('support@shu.ai')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /chat with shu assistant/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/have a question on how to use the app/i)).not.toBeInTheDocument();
  });
});
