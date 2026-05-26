import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import AssistantPhaseContent from '../AssistantPhaseContent';
import { PLACEHOLDER_THINKING } from '../utils/chatConfig';

// Stub the three children so we can assert which branch the
// AssistantPhaseContent decision tree picks without rendering the
// actual ThinkingIndicator / MessageContent / StreamingFeather trees.
vi.mock('../ThinkingIndicator', () => ({
  default: ({ message }) => <div data-testid="thinking-indicator" data-pool={message?.thinkingPool ?? ''} />,
}));
vi.mock('../MessageContent', () => ({
  default: ({ message }) => <div data-testid="message-content">{message?.content}</div>,
}));
vi.mock('../StreamingFeather', () => ({
  default: ({ isStreaming }) => <div data-testid="streaming-feather" data-streaming={String(Boolean(isStreaming))} />,
}));

const TestWrapper = ({ children }) => {
  const theme = createTheme();
  return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
};

const renderForVariant = (variant, { hasReasoning = false } = {}) => {
  const theme = createTheme();
  return render(
    <AssistantPhaseContent
      variant={variant}
      hasReasoning={hasReasoning}
      theme={theme}
      isDarkMode={false}
      userBubbleText="#000"
      assistantLinkColor="#1976d2"
      parseDocumentHref={() => null}
      onOpenDocument={() => {}}
      attachmentChipStyles={{}}
    />,
    { wrapper: TestWrapper }
  );
};

describe('AssistantPhaseContent — thinking phase', () => {
  it('renders ThinkingIndicator alone when streaming, content is the placeholder, and no reasoning', () => {
    renderForVariant({
      isStreaming: true,
      content: PLACEHOLDER_THINKING,
    });
    expect(screen.getByTestId('thinking-indicator')).toBeInTheDocument();
    expect(screen.queryByTestId('message-content')).not.toBeInTheDocument();
    expect(screen.queryByTestId('streaming-feather')).not.toBeInTheDocument();
  });

  it('passes the thinkingPool through to ThinkingIndicator', () => {
    renderForVariant({
      isStreaming: true,
      content: PLACEHOLDER_THINKING,
      thinkingPool: 'rag',
    });
    expect(screen.getByTestId('thinking-indicator')).toHaveAttribute('data-pool', 'rag');
  });
});

describe('AssistantPhaseContent — streaming phase', () => {
  it('renders MessageContent + StreamingFeather when content has streamed in', () => {
    renderForVariant({
      isStreaming: true,
      content: 'partial response',
    });
    expect(screen.queryByTestId('thinking-indicator')).not.toBeInTheDocument();
    expect(screen.getByTestId('message-content')).toBeInTheDocument();
    expect(screen.getByTestId('streaming-feather')).toHaveAttribute('data-streaming', 'true');
  });

  it('exits the thinking phase when reasoning arrives before content', () => {
    renderForVariant(
      {
        isStreaming: true,
        content: PLACEHOLDER_THINKING,
      },
      { hasReasoning: true }
    );
    expect(screen.queryByTestId('thinking-indicator')).not.toBeInTheDocument();
    expect(screen.getByTestId('message-content')).toBeInTheDocument();
    expect(screen.getByTestId('streaming-feather')).toHaveAttribute('data-streaming', 'true');
  });
});

describe('AssistantPhaseContent — done phase', () => {
  it('renders MessageContent + StreamingFeather (which self-unmounts) when the stream has ended', () => {
    renderForVariant({
      isStreaming: false,
      content: 'final response',
    });
    expect(screen.queryByTestId('thinking-indicator')).not.toBeInTheDocument();
    expect(screen.getByTestId('message-content')).toBeInTheDocument();
    // StreamingFeather is rendered with isStreaming=false; the real
    // component returns null in that state. We verify the prop is
    // passed through correctly so the self-unmount can happen.
    expect(screen.getByTestId('streaming-feather')).toHaveAttribute('data-streaming', 'false');
  });
});
