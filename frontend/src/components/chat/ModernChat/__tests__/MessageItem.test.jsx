/**
 * SHU-803 Vitest coverage for MessageItem — "Stopped by user" caption.
 *
 * The Stop button itself lives in the input bar (see InputBar.test.jsx)
 * so it stays visible regardless of scroll position. MessageItem only
 * owns the persisted "Stopped by user" caption that surfaces once the
 * placeholder flips out of isStreaming.
 *
 * The interesting cases here:
 * - The caption renders for non-streaming messages whose
 *   ``message_metadata.stream_state`` is ``user_terminated`` or
 *   ``shutdown`` (AC6), and is ABSENT for ``complete`` /
 *   ``client_disconnected`` (AC7).
 */

import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { vi } from 'vitest';
import MessageItem from '../MessageItem';

// MessageContent makes its own renderer calls; stub it out so we focus
// on the surrounding chrome under test here.
vi.mock('../MessageContent', () => ({
  default: ({ message }) => <div data-testid="message-content">{message?.content ?? ''}</div>,
}));

vi.mock('../../shared/UserAvatar.jsx', () => ({
  default: () => <div data-testid="user-avatar" />,
}));

const TestWrapper = ({ children }) => {
  const theme = createTheme();
  return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
};

const baseProps = (overrides = {}) => ({
  user: { id: 'test-user', email: 'test@example.com' },
  theme: createTheme(),
  chatStyles: {
    userBubbleBg: '#fff',
    userBubbleText: '#000',
    assistantBubbleBg: '#eee',
    assistantBubbleBorder: '1px solid #ccc',
    assistantLinkColor: '#1976d2',
    isDarkMode: false,
  },
  attachmentChipStyles: {},
  variantGroups: {},
  variantSelection: {},
  onVariantChange: vi.fn(),
  onRegenerate: vi.fn(),
  onCopy: vi.fn(),
  isVariantGroupStreaming: () => false,
  parseDocumentHref: () => null,
  onOpenDocument: vi.fn(),
  fallbackModelConfig: null,
  regenerationRequests: new Map(),
  onToggleReasoning: vi.fn(),
  ...overrides,
});

const makeAssistantMessage = (overrides = {}) => ({
  id: 'msg-1',
  conversation_id: 'conv-1',
  role: 'assistant',
  content: 'hello world',
  created_at: '2026-05-20T17:00:00Z',
  parent_message_id: null,
  variant_index: 0,
  message_metadata: {},
  ...overrides,
});

describe('MessageItem — SHU-803 "Stopped by user" caption (AC6/AC7)', () => {
  it('AC6: renders the caption for stream_state="user_terminated"', () => {
    const message = makeAssistantMessage({
      isStreaming: false,
      content: 'partial answer',
      message_metadata: { stream_state: 'user_terminated' },
    });
    render(
      <TestWrapper>
        <MessageItem message={message} {...baseProps()} />
      </TestWrapper>
    );
    expect(screen.getByText('Stopped by user')).toBeInTheDocument();
  });

  it('AC6: renders the caption for stream_state="shutdown" (server-initiated)', () => {
    // Per AC6: server-initiated stop looks identical to user-initiated
    // from the reader's perspective — partial content, here's what we got.
    const message = makeAssistantMessage({
      isStreaming: false,
      content: 'partial answer cut by shutdown',
      message_metadata: { stream_state: 'shutdown' },
    });
    render(
      <TestWrapper>
        <MessageItem message={message} {...baseProps()} />
      </TestWrapper>
    );
    expect(screen.getByText('Stopped by user')).toBeInTheDocument();
  });

  it('AC7: no caption for stream_state="complete"', () => {
    const message = makeAssistantMessage({
      isStreaming: false,
      message_metadata: { stream_state: 'complete' },
    });
    render(
      <TestWrapper>
        <MessageItem message={message} {...baseProps()} />
      </TestWrapper>
    );
    expect(screen.queryByText('Stopped by user')).toBeNull();
  });

  it('AC7: no caption for stream_state="client_disconnected" (whole point of disconnect-survival)', () => {
    const message = makeAssistantMessage({
      isStreaming: false,
      message_metadata: { stream_state: 'client_disconnected' },
    });
    render(
      <TestWrapper>
        <MessageItem message={message} {...baseProps()} />
      </TestWrapper>
    );
    expect(screen.queryByText('Stopped by user')).toBeNull();
  });

  it('no caption while the message is still streaming (live spinner owns the UI)', () => {
    // While streaming, the caption is suppressed — the live spinner is
    // the active UI. The caption only surfaces once the placeholder
    // flips isStreaming=false (either via final_message or the AC5
    // optimistic flip).
    const message = makeAssistantMessage({
      isStreaming: true,
      streamId: 'stream-xyz',
      message_metadata: { stream_state: 'user_terminated' },
    });
    render(
      <TestWrapper>
        <MessageItem message={message} {...baseProps()} />
      </TestWrapper>
    );
    expect(screen.queryByText('Stopped by user')).toBeNull();
  });

  it('no caption when message_metadata is null / undefined (e.g. legacy or user messages)', () => {
    const message = makeAssistantMessage({
      isStreaming: false,
      message_metadata: null,
    });
    render(
      <TestWrapper>
        <MessageItem message={message} {...baseProps()} />
      </TestWrapper>
    );
    expect(screen.queryByText('Stopped by user')).toBeNull();
  });
});
