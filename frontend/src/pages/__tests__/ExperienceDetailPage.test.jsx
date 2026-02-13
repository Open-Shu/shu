import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { vi } from 'vitest';
import ExperienceDetailPage from '../ExperienceDetailPage';
import * as api from '../../services/api';

// Mock the API
vi.mock('../../services/api', () => ({
  experiencesAPI: {
    getMyResults: vi.fn(),
  },
  chatAPI: {
    createConversationFromExperience: vi.fn(),
  },
  extractDataFromResponse: vi.fn(),
  formatError: vi.fn((err) => err?.message || 'An error occurred'),
}));

// Mock ExperienceRunDialog to capture its props
vi.mock('../../components/ExperienceRunDialog', () => ({
  default: ({ open, experienceId, experienceName }) =>
    open ? (
      <div data-testid="run-dialog" data-experience-id={experienceId} data-experience-name={experienceName}>
        Run Dialog for {experienceName}
      </div>
    ) : null,
}));

// Mock MarkdownRenderer to simplify rendering
vi.mock('../../components/shared/MarkdownRenderer', () => ({
  default: ({ content }) => <div data-testid="markdown-renderer">{content}</div>,
}));

// Mock timezoneFormatter
vi.mock('../../utils/timezoneFormatter', () => ({
  formatDateTimeFull: vi.fn(() => 'January 1, 2025 at 12:00 AM UTC'),
}));

// Mock log utility
vi.mock('../../utils/log', () => ({
  default: {
    info: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
  },
}));

const theme = createTheme();

const renderWithProviders = (experienceId = 'exp-1') => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/dashboard/experience/${experienceId}`]}>
        <ThemeProvider theme={theme}>
          <Routes>
            <Route path="/dashboard/experience/:experienceId" element={<ExperienceDetailPage />} />
          </Routes>
        </ThemeProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const makeExperience = (overrides = {}) => ({
  experience_id: 'exp-1',
  experience_name: 'Test Experience',
  prompt_template: 'Do something interesting',
  latest_run_id: 'run-1',
  latest_run_finished_at: '2025-01-01T00:00:00Z',
  result_preview: 'Some result data',
  can_run: true,
  missing_identities: [],
  ...overrides,
});

describe('ExperienceDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const setupWithExperiences = (experiences) => {
    const responseData = { experiences, scheduled_count: 0 };
    api.experiencesAPI.getMyResults.mockResolvedValue({ data: responseData });
    api.extractDataFromResponse.mockReturnValue(responseData);
  };

  test('Run button renders alongside Start Conversation when can_run=true', async () => {
    const experience = makeExperience({ can_run: true, result_preview: 'Some content' });
    setupWithExperiences([experience]);

    renderWithProviders('exp-1');

    await waitFor(() => {
      expect(screen.getByText('Test Experience')).toBeInTheDocument();
    });

    // Run button should be rendered and enabled
    const runButton = screen.getByRole('button', { name: /run/i });
    expect(runButton).toBeInTheDocument();
    expect(runButton).not.toBeDisabled();

    // Start Conversation button should also be rendered
    const conversationButton = screen.getByRole('button', { name: /start conversation/i });
    expect(conversationButton).toBeInTheDocument();
  });

  test('Run button is disabled when can_run=false', async () => {
    const experience = makeExperience({ can_run: false });
    setupWithExperiences([experience]);

    renderWithProviders('exp-1');

    await waitFor(() => {
      expect(screen.getByText('Test Experience')).toBeInTheDocument();
    });

    // Run button should be rendered but disabled
    const runButton = screen.getByRole('button', { name: /^run$/i });
    expect(runButton).toBeInTheDocument();
    expect(runButton).toBeDisabled();
  });

  test('Missing identities warning renders when can_run=false with missing_identities', async () => {
    const experience = makeExperience({
      can_run: false,
      missing_identities: ['Slack', 'GitHub'],
    });
    setupWithExperiences([experience]);

    renderWithProviders('exp-1');

    await waitFor(() => {
      expect(screen.getByText('Test Experience')).toBeInTheDocument();
    });

    // The Alert with missing identities should be displayed
    expect(screen.getByText('Missing required connections: Slack, GitHub')).toBeInTheDocument();
  });

  test('Clicking Run button opens ExperienceRunDialog with correct props', async () => {
    const experience = makeExperience({
      experience_id: 'exp-42',
      experience_name: 'My Research Bot',
      can_run: true,
    });
    setupWithExperiences([experience]);

    renderWithProviders('exp-42');

    await waitFor(() => {
      expect(screen.getByText('My Research Bot')).toBeInTheDocument();
    });

    // Dialog should not be open initially
    expect(screen.queryByTestId('run-dialog')).not.toBeInTheDocument();

    // Click the Run button
    const runButton = screen.getByRole('button', { name: /^run$/i });
    fireEvent.click(runButton);

    // The dialog mock should appear with correct props
    const dialog = screen.getByTestId('run-dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute('data-experience-id', 'exp-42');
    expect(dialog).toHaveAttribute('data-experience-name', 'My Research Bot');
  });
});
