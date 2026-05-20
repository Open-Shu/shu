/**
 * SHU-803 Vitest coverage for MessageItem — Stop-button toolbar and
 * "Stopped by user" caption behavior.
 *
 * The interesting cases:
 * - Stop button renders only while ``variant.isStreaming``.
 * - Stop button is disabled (not hidden) when ``variant.streamId`` is
 *   missing — that's the ~10-50ms window after the placeholder lands
 *   but before ``stream_start`` arrives (AC8).
 * - Click fires ``onStop(variant)`` and shows in-flight state (AC3).
 * - Double-click is debounced via the local ``stopping`` state.
 * - The "Stopped by user" caption renders for non-streaming messages
 *   whose ``message_metadata.stream_state`` is ``user_terminated`` or
 *   ``shutdown`` (AC6), and is ABSENT for ``complete`` /
 *   ``client_disconnected`` (AC7).
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

describe('MessageItem — SHU-803 Stop button (AC2/3/4/5/8)', () => {
  it('does not render the Stop button for a non-streaming message', () => {
    const message = makeAssistantMessage({ isStreaming: false });
    render(
      <TestWrapper>
        <MessageItem message={message} onStop={vi.fn()} {...baseProps()} />
      </TestWrapper>
    );
    expect(screen.queryByRole('button', { name: /stop generating/i })).toBeNull();
  });

  it('renders the Stop button while streaming with a captured stream_id', () => {
    const message = makeAssistantMessage({ isStreaming: true, streamId: 'stream-xyz' });
    render(
      <TestWrapper>
        <MessageItem message={message} onStop={vi.fn()} {...baseProps()} />
      </TestWrapper>
    );
    const button = screen.getByRole('button', { name: /stop generating/i });
    expect(button).toBeInTheDocument();
    expect(button).not.toBeDisabled();
  });

  it('AC8: Stop button is disabled (not hidden) while streaming without a stream_id', () => {
    // The ~10-50ms window between placeholder creation and `stream_start`.
    // Disabled state with the "Initializing…" tooltip is more
    // discoverable than an invisible button per the SHU-803 plan
    // Decisions Log.
    const message = makeAssistantMessage({ isStreaming: true, streamId: undefined });
    render(
      <TestWrapper>
        <MessageItem message={message} onStop={vi.fn()} {...baseProps()} />
      </TestWrapper>
    );
    const button = screen.getByRole('button', { name: /stop generating/i });
    expect(button).toBeInTheDocument();
    expect(button).toBeDisabled();
  });

  it('AC4: clicking Stop calls onStop with the variant', async () => {
    const onStop = vi.fn().mockResolvedValue(undefined);
    const message = makeAssistantMessage({ isStreaming: true, streamId: 'stream-xyz' });
    render(
      <TestWrapper>
        <MessageItem message={message} onStop={onStop} {...baseProps()} />
      </TestWrapper>
    );
    const button = screen.getByRole('button', { name: /stop generating/i });
    fireEvent.click(button);
    await waitFor(() => expect(onStop).toHaveBeenCalledTimes(1));
    // The variant object (a clone of the message) was passed.
    expect(onStop).toHaveBeenCalledWith(expect.objectContaining({ id: 'msg-1', streamId: 'stream-xyz' }));
  });

  it('AC3: double-click is debounced via the local stopping state', async () => {
    // Hold the promise open so the button stays in `stopping=true`
    // for the duration of the test. Two rapid clicks should fire only
    // one onStop call.
    let resolveStop;
    const onStop = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveStop = resolve;
        })
    );
    const message = makeAssistantMessage({ isStreaming: true, streamId: 'stream-xyz' });
    render(
      <TestWrapper>
        <MessageItem message={message} onStop={onStop} {...baseProps()} />
      </TestWrapper>
    );
    const button = screen.getByRole('button', { name: /stop generating/i });
    fireEvent.click(button);
    fireEvent.click(button);
    // Wait for the first click's state update to settle.
    await waitFor(() => expect(button).toBeDisabled());
    expect(onStop).toHaveBeenCalledTimes(1);
    // Release the promise so the component doesn't leak.
    resolveStop?.();
  });

  it('does not call onStop when onStop is not provided', () => {
    const message = makeAssistantMessage({ isStreaming: true, streamId: 'stream-xyz' });
    render(
      <TestWrapper>
        <MessageItem message={message} onStop={undefined} {...baseProps()} />
      </TestWrapper>
    );
    const button = screen.getByRole('button', { name: /stop generating/i });
    // Disabled when no onStop handler — defensive against integration regressions.
    expect(button).toBeDisabled();
  });
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
        <MessageItem message={message} onStop={vi.fn()} {...baseProps()} />
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
        <MessageItem message={message} onStop={vi.fn()} {...baseProps()} />
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
        <MessageItem message={message} onStop={vi.fn()} {...baseProps()} />
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
        <MessageItem message={message} onStop={vi.fn()} {...baseProps()} />
      </TestWrapper>
    );
    expect(screen.queryByText('Stopped by user')).toBeNull();
  });

  it('no caption while the message is still streaming (live spinner owns the UI)', () => {
    // While streaming, the caption is suppressed — the Stop button and
    // spinner are the active UI. The caption only surfaces once the
    // placeholder flips isStreaming=false (either via final_message or
    // the AC5 optimistic flip).
    const message = makeAssistantMessage({
      isStreaming: true,
      streamId: 'stream-xyz',
      message_metadata: { stream_state: 'user_terminated' },
    });
    render(
      <TestWrapper>
        <MessageItem message={message} onStop={vi.fn()} {...baseProps()} />
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
        <MessageItem message={message} onStop={vi.fn()} {...baseProps()} />
      </TestWrapper>
    );
    expect(screen.queryByText('Stopped by user')).toBeNull();
  });
});
