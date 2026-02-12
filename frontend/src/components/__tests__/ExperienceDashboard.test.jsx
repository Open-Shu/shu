import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider } from '@mui/material/styles';
import { createTheme } from '@mui/material/styles';
import { vi } from 'vitest';
import ExperienceDashboard from '../ExperienceDashboard';
import * as api from '../../services/api';

// Mock the API
vi.mock('../../services/api', () => ({
  experiencesAPI: {
    getMyResults: vi.fn(),
  },
  extractDataFromResponse: vi.fn(),
  formatError: vi.fn((err) => err?.message || 'An error occurred'),
}));

// Mock ExperienceRunDialog to capture its props
vi.mock('../ExperienceRunDialog', () => ({
  default: ({ open, experienceId, experienceName }) =>
    open ? (
      <div data-testid="run-dialog" data-experience-id={experienceId} data-experience-name={experienceName}>
        Run Dialog for {experienceName}
      </div>
    ) : null,
}));

// Mock date-fns to return a fixed string
vi.mock('date-fns', () => ({
  formatDistanceToNow: vi.fn(() => '5 minutes ago'),
}));

const theme = createTheme();

const renderWithProviders = (component) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ThemeProvider theme={theme}>{component}</ThemeProvider>
      </BrowserRouter>
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

describe('ExperienceDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const setupWithExperiences = (experiences) => {
    const responseData = { experiences, scheduled_count: 0 };
    api.experiencesAPI.getMyResults.mockResolvedValue({ data: responseData });
    api.extractDataFromResponse.mockReturnValue(responseData);
  };

  test('Run button renders on card when can_run=true', async () => {
    const experience = makeExperience({ can_run: true });
    setupWithExperiences([experience]);

    renderWithProviders(
      <ExperienceDashboard
        onCreateConversation={vi.fn()}
        createConversationDisabled={false}
        onExperienceClick={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Test Experience')).toBeInTheDocument();
    });

    // The play button should be rendered and enabled
    const playButton = screen.getByRole('button', { name: /run experience/i });
    expect(playButton).toBeInTheDocument();
    expect(playButton).not.toBeDisabled();
  });

  test('Run button is disabled when can_run=false', async () => {
    const experience = makeExperience({ can_run: false });
    setupWithExperiences([experience]);

    renderWithProviders(
      <ExperienceDashboard
        onCreateConversation={vi.fn()}
        createConversationDisabled={false}
        onExperienceClick={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Test Experience')).toBeInTheDocument();
    });

    // Find the play icon and its parent IconButton
    const playIcon = screen.getByTestId('PlayArrowIcon');
    const playButton = playIcon.closest('button');
    expect(playButton).toBeInTheDocument();
    expect(playButton).toBeDisabled();
  });

  test('Clicking Run button opens ExperienceRunDialog with correct props', async () => {
    const experience = makeExperience({
      experience_id: 'exp-42',
      experience_name: 'My Research Bot',
      can_run: true,
    });
    setupWithExperiences([experience]);

    renderWithProviders(
      <ExperienceDashboard
        onCreateConversation={vi.fn()}
        createConversationDisabled={false}
        onExperienceClick={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('My Research Bot')).toBeInTheDocument();
    });

    // Dialog should not be open initially
    expect(screen.queryByTestId('run-dialog')).not.toBeInTheDocument();

    // Click the actual IconButton (find via the PlayArrow icon and navigate to parent button)
    const playIcon = screen.getByTestId('PlayArrowIcon');
    const playButton = playIcon.closest('button');
    fireEvent.click(playButton);

    // The dialog mock should appear with correct props
    const dialog = screen.getByTestId('run-dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute('data-experience-id', 'exp-42');
    expect(dialog).toHaveAttribute('data-experience-name', 'My Research Bot');
  });

  test('Missing identities warning renders when can_run=false with missing_identities', async () => {
    const experience = makeExperience({
      can_run: false,
      missing_identities: ['Slack', 'GitHub'],
    });
    setupWithExperiences([experience]);

    renderWithProviders(
      <ExperienceDashboard
        onCreateConversation={vi.fn()}
        createConversationDisabled={false}
        onExperienceClick={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Test Experience')).toBeInTheDocument();
    });

    // The Alert with missing identities should be displayed
    expect(screen.getByText('Missing required connections: Slack, GitHub')).toBeInTheDocument();
  });
});
