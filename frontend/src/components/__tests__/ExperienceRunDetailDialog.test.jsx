import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from 'react-query';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { vi } from 'vitest';
import ExperienceRunDetailDialog from '../ExperienceRunDetailDialog';
import * as api from '../../services/api';

// Mock dependencies
vi.mock('../../services/api', () => ({
  experiencesAPI: {
    getRun: vi.fn(),
  },
  conversationsAPI: {
    createFromRun: vi.fn(),
  },
  extractDataFromResponse: vi.fn((response) => response.data),
  formatError: vi.fn((error) => error.message || 'An error occurred'),
}));

vi.mock('../../utils/log', () => ({
  default: {
    info: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
  },
}));

// Mock MarkdownRenderer to avoid react-markdown import issues
vi.mock('../shared/MarkdownRenderer', () => ({
  default: function MarkdownRenderer({ content }) {
    return <div data-testid="markdown-renderer">{content}</div>;
  },
}));

// Mock StepStatusIcon
vi.mock('../StepStatusIcon', () => ({
  default: function StepStatusIcon() {
    return <div data-testid="step-status-icon" />;
  },
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Test wrapper component
const TestWrapper = ({ children }) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  const theme = createTheme();

  return (
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider theme={theme}>{children}</ThemeProvider>
      </QueryClientProvider>
    </BrowserRouter>
  );
};

describe('ExperienceRunDetailDialog - Start Conversation Button', () => {
  const mockRunWithContent = {
    id: 'run-123',
    status: 'succeeded',
    started_at: '2024-01-15T10:00:00Z',
    result_content: '# Morning Briefing\n\nHere is your briefing for today...',
    user: {
      email: 'test@example.com',
    },
    step_states: {},
  };

  const mockRunWithoutContent = {
    id: 'run-456',
    status: 'succeeded',
    started_at: '2024-01-15T10:00:00Z',
    result_content: null,
    user: {
      email: 'test@example.com',
    },
    step_states: {},
  };

  const mockConversation = {
    id: 'conv-789',
    title: 'Morning Briefing',
    created_at: '2024-01-15T10:05:00Z',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
  });

  test('renders Start Conversation button when run has result content', async () => {
    api.experiencesAPI.getRun.mockResolvedValue({ data: mockRunWithContent });

    render(
      <TestWrapper>
        <ExperienceRunDetailDialog open={true} onClose={jest.fn()} runId="run-123" timezone="America/New_York" />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Start Conversation')).toBeInTheDocument();
    });

    const button = screen.getByRole('button', { name: /start conversation/i });
    expect(button).toBeInTheDocument();
    expect(button).not.toBeDisabled();
  });

  test('does not render Start Conversation button when run has no result content', async () => {
    api.experiencesAPI.getRun.mockResolvedValue({ data: mockRunWithoutContent });

    render(
      <TestWrapper>
        <ExperienceRunDetailDialog open={true} onClose={jest.fn()} runId="run-456" timezone="America/New_York" />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Run Details')).toBeInTheDocument();
    });

    const button = screen.queryByRole('button', {
      name: /start conversation/i,
    });
    expect(button).not.toBeInTheDocument();
  });

  test('shows loading state during conversation creation', async () => {
    api.experiencesAPI.getRun.mockResolvedValue({ data: mockRunWithContent });

    // Mock a delayed response
    api.chatAPI = {
      createConversationFromExperience: jest
        .fn()
        .mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve({ data: mockConversation }), 100))),
    };

    render(
      <TestWrapper>
        <ExperienceRunDetailDialog open={true} onClose={jest.fn()} runId="run-123" timezone="America/New_York" />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Start Conversation')).toBeInTheDocument();
    });

    const button = screen.getByRole('button', { name: /start conversation/i });
    fireEvent.click(button);

    // Check loading state
    await waitFor(() => {
      expect(screen.getByText('Starting...')).toBeInTheDocument();
    });

    const loadingButton = screen.getByRole('button', { name: /starting/i });
    expect(loadingButton).toBeDisabled();
  });

  test('navigates to conversation view on successful creation', async () => {
    const mockOnClose = jest.fn();

    api.experiencesAPI.getRun.mockResolvedValue({ data: mockRunWithContent });

    api.chatAPI = {
      createConversationFromExperience: jest.fn().mockResolvedValue({ data: mockConversation }),
    };

    render(
      <TestWrapper>
        <ExperienceRunDetailDialog open={true} onClose={mockOnClose} runId="run-123" timezone="America/New_York" />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Start Conversation')).toBeInTheDocument();
    });

    const button = screen.getByRole('button', { name: /start conversation/i });
    fireEvent.click(button);

    await waitFor(() => {
      expect(api.chatAPI.createConversationFromExperience).toHaveBeenCalledWith('run-123');
    });

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/chat?conversationId=conv-789');
      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  test('displays error message on conversation creation failure', async () => {
    api.experiencesAPI.getRun.mockResolvedValue({ data: mockRunWithContent });

    const errorMessage = 'Failed to create conversation';
    api.chatAPI = {
      createConversationFromExperience: jest.fn().mockRejectedValue(new Error(errorMessage)),
    };

    render(
      <TestWrapper>
        <ExperienceRunDetailDialog open={true} onClose={jest.fn()} runId="run-123" timezone="America/New_York" />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Start Conversation')).toBeInTheDocument();
    });

    const button = screen.getByRole('button', { name: /start conversation/i });
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText(errorMessage)).toBeInTheDocument();
    });

    // Button should be enabled again after error
    expect(button).not.toBeDisabled();
  });

  test('button is disabled during conversation creation', async () => {
    api.experiencesAPI.getRun.mockResolvedValue({ data: mockRunWithContent });

    // Mock a delayed response to test disabled state
    api.chatAPI = {
      createConversationFromExperience: jest
        .fn()
        .mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve({ data: mockConversation }), 200))),
    };

    render(
      <TestWrapper>
        <ExperienceRunDetailDialog open={true} onClose={jest.fn()} runId="run-123" timezone="America/New_York" />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Start Conversation')).toBeInTheDocument();
    });

    const button = screen.getByRole('button', { name: /start conversation/i });
    expect(button).not.toBeDisabled();

    fireEvent.click(button);

    // Button should be disabled during creation
    await waitFor(() => {
      const disabledButton = screen.getByRole('button', { name: /starting/i });
      expect(disabledButton).toBeDisabled();
    });
  });

  test('error alert can be dismissed', async () => {
    api.experiencesAPI.getRun.mockResolvedValue({ data: mockRunWithContent });

    const errorMessage = 'Network error';
    api.chatAPI = {
      createConversationFromExperience: jest.fn().mockRejectedValue(new Error(errorMessage)),
    };

    render(
      <TestWrapper>
        <ExperienceRunDetailDialog open={true} onClose={jest.fn()} runId="run-123" timezone="America/New_York" />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Start Conversation')).toBeInTheDocument();
    });

    const button = screen.getByRole('button', { name: /start conversation/i });
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText(errorMessage)).toBeInTheDocument();
    });

    // Find the alert close button specifically (not the dialog close button)
    const alertCloseButtons = screen
      .getAllByRole('button')
      .filter((btn) => btn.getAttribute('aria-label') === 'Close' && btn.getAttribute('title') === 'Close');

    expect(alertCloseButtons.length).toBeGreaterThan(0);
    fireEvent.click(alertCloseButtons[0]);

    await waitFor(() => {
      expect(screen.queryByText(errorMessage)).not.toBeInTheDocument();
    });
  });

  test('calls API with correct runId parameter', async () => {
    const testRunId = 'test-run-id-123';

    api.experiencesAPI.getRun.mockResolvedValue({ data: mockRunWithContent });

    api.chatAPI = {
      createConversationFromExperience: jest.fn().mockResolvedValue({ data: mockConversation }),
    };

    render(
      <TestWrapper>
        <ExperienceRunDetailDialog open={true} onClose={jest.fn()} runId={testRunId} timezone="America/New_York" />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Start Conversation')).toBeInTheDocument();
    });

    const button = screen.getByRole('button', { name: /start conversation/i });
    fireEvent.click(button);

    await waitFor(() => {
      expect(api.chatAPI.createConversationFromExperience).toHaveBeenCalledWith(testRunId);
      expect(mockNavigate).toHaveBeenCalledWith('/chat?conversationId=conv-789');
    });
  });
});
