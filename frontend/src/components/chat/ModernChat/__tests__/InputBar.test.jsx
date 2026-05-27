/**
 * SHU-803 Vitest coverage for InputBar — Send <-> Stop swap during
 * streaming. The Stop button replaces the Send button (same screen
 * position) while a stream is in flight; this keeps the action within
 * thumb reach on mobile and at a predictable location on desktop
 * regardless of how far the streaming bubble has scrolled.
 */

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { vi } from 'vitest';
import InputBar from '../InputBar';

// BrainIcon/BrainPopover render heavy theme-driven decoration that
// isn't part of the swap under test; mock them out to keep the test
// fast and focused.
vi.mock('../BrainIcon', () => ({
  default: () => <div data-testid="brain-icon" />,
}));
vi.mock('../BrainPopover', () => ({
  default: () => null,
}));

const TestWrapper = ({ children }) => {
  const theme = createTheme();
  return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
};
TestWrapper.displayName = 'InputBarTestThemeWrapper';

const baseProps = (overrides = {}) => ({
  pendingAttachments: [],
  onRemoveAttachment: vi.fn(),
  attachmentChipStyles: {},
  inputMessage: '',
  onInputChange: vi.fn(),
  onKeyDown: vi.fn(),
  onSend: vi.fn(),
  sendDisabled: false,
  inputRef: { current: null },
  fileInputRef: { current: null },
  onFileSelected: vi.fn(),
  plusAnchorEl: null,
  onPlusOpen: vi.fn(),
  onPlusClose: vi.fn(),
  isUploadingAttachment: false,
  onOpenPluginPicker: vi.fn(),
  pluginsEnabled: false,
  onUploadClick: vi.fn(),
  onOpenKBPicker: vi.fn(),
  selectedKBs: [],
  onRemoveKB: vi.fn(),
  onSelectEnsembleMode: undefined,
  isEnsembleModeActive: false,
  ensembleModeLabel: null,
  onClearEnsembleMode: undefined,
  ensembleMenuDisabled: false,
  personalKB: null,
  personalKBLoading: false,
  personalKBUploading: false,
  personalKBErrors: [],
  onUploadToPersonalKB: vi.fn(),
  onRetryPersonalKBFile: vi.fn(),
  onDismissPersonalKBError: vi.fn(),
  isStreaming: false,
  canStop: false,
  onStop: undefined,
  isMobile: false,
  ...overrides,
});

describe('InputBar — SHU-803 Send/Stop swap', () => {
  it('shows the Send button (and no Stop button) when not streaming', () => {
    render(
      <TestWrapper>
        <InputBar {...baseProps({ inputMessage: 'hi' })} />
      </TestWrapper>
    );
    expect(screen.getByRole('button', { name: /^send/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /stop generating/i })).toBeNull();
  });

  it('replaces Send with Stop while streaming', () => {
    render(
      <TestWrapper>
        <InputBar {...baseProps({ isStreaming: true, canStop: true, onStop: vi.fn() })} />
      </TestWrapper>
    );
    expect(screen.queryByRole('button', { name: /^send/i })).toBeNull();
    const stopButton = screen.getByRole('button', { name: /stop generating/i });
    expect(stopButton).toBeInTheDocument();
    expect(stopButton).not.toBeDisabled();
  });

  it('disables Stop while streaming but stream_id has not yet arrived', () => {
    // canStop=false maps to the ~10-50ms window between Send and
    // stream_start. We keep the button visible (with "Initializing…"
    // tooltip) rather than hiding it so the user has consistent feedback.
    render(
      <TestWrapper>
        <InputBar {...baseProps({ isStreaming: true, canStop: false, onStop: vi.fn() })} />
      </TestWrapper>
    );
    const stopButton = screen.getByRole('button', { name: /stop generating/i });
    expect(stopButton).toBeDisabled();
  });

  it('clicking Stop invokes onStop', async () => {
    const onStop = vi.fn().mockResolvedValue(undefined);
    render(
      <TestWrapper>
        <InputBar {...baseProps({ isStreaming: true, canStop: true, onStop })} />
      </TestWrapper>
    );
    fireEvent.click(screen.getByRole('button', { name: /stop generating/i }));
    await waitFor(() => expect(onStop).toHaveBeenCalledTimes(1));
  });

  it('double-click is debounced via the local stopping state', async () => {
    let resolveStop;
    const onStop = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveStop = resolve;
        })
    );
    render(
      <TestWrapper>
        <InputBar {...baseProps({ isStreaming: true, canStop: true, onStop })} />
      </TestWrapper>
    );
    const stopButton = screen.getByRole('button', { name: /stop generating/i });
    fireEvent.click(stopButton);
    fireEvent.click(stopButton);
    await waitFor(() => expect(stopButton).toBeDisabled());
    expect(onStop).toHaveBeenCalledTimes(1);
    resolveStop?.();
  });

  it('mobile variant: Stop renders as an icon-only IconButton', () => {
    render(
      <TestWrapper>
        <InputBar {...baseProps({ isStreaming: true, canStop: true, onStop: vi.fn(), isMobile: true })} />
      </TestWrapper>
    );
    const stopButton = screen.getByRole('button', { name: /stop generating/i });
    expect(stopButton).toBeInTheDocument();
    // The mobile variant has no visible "Stop" label text — just the icon.
    expect(within(stopButton).queryByText('Stop')).toBeNull();
  });

  it('Stop is disabled when no onStop handler is wired', () => {
    render(
      <TestWrapper>
        <InputBar {...baseProps({ isStreaming: true, canStop: true, onStop: undefined })} />
      </TestWrapper>
    );
    expect(screen.getByRole('button', { name: /stop generating/i })).toBeDisabled();
  });
});
