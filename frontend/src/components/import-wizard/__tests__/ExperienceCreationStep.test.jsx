import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { BrowserRouter } from 'react-router-dom';
import { vi } from 'vitest';
import ExperienceCreationStep from '../ExperienceCreationStep';
import * as api from '../../../services/api';

// Mock baseUrl first to prevent module resolution issues
vi.mock('../../../services/baseUrl', () => ({
  getApiV1Base: vi.fn(() => 'http://localhost:8000/api/v1'),
  getApiBaseUrl: vi.fn(() => 'http://localhost:8000'),
  getWsBaseUrl: vi.fn(() => 'ws://localhost:8000'),
}));

// Mock the logger before other imports
vi.mock('../../../utils/log', () => {
  const mockLog = {
    info: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
  return {
    default: mockLog,
    log: mockLog,
  };
});

// Mock the API service
vi.mock('../../../services/api', () => ({
  experiencesAPI: {
    create: vi.fn(),
  },
  extractDataFromResponse: vi.fn((response) => response.data),
  formatError: vi.fn((error) => error.message || 'Unknown error'),
}));

// Mock the YAML processor
vi.mock('../../../services/yamlProcessor', () => ({
  convertToExperiencePayload: vi.fn(() => ({
    name: 'Test Experience',
    description: 'Test Description',
    steps: [],
  })),
}));

const createTestQueryClient = () => {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
};

const renderWithProviders = (component) => {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{component}</BrowserRouter>
    </QueryClientProvider>
  );
};

describe('ExperienceCreationStep', () => {
  const defaultProps = {
    yamlContent: 'name: Test\ndescription: Test experience',
    resolvedValues: { model_configuration_id: 'config-1' },
    onCreationComplete: vi.fn(),
    onRetry: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('renders creation step title', () => {
    renderWithProviders(<ExperienceCreationStep {...defaultProps} />);

    expect(screen.getByText('Create Experience')).toBeInTheDocument();
    expect(screen.getByText(/Creating your experience from the configured YAML/)).toBeInTheDocument();
  });

  test('shows idle state initially', () => {
    renderWithProviders(
      <ExperienceCreationStep
        {...defaultProps}
        yamlContent="" // No YAML content, so should stay idle
      />
    );

    expect(screen.getByText('Preparing to create experience...')).toBeInTheDocument();
  });

  test('handles API error during creation', async () => {
    api.experiencesAPI.create.mockRejectedValue(new Error('API Error'));

    renderWithProviders(<ExperienceCreationStep {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Creation Failed')).toBeInTheDocument();
    });
  });

  test('handles missing YAML content', async () => {
    renderWithProviders(
      <ExperienceCreationStep
        {...defaultProps}
        yamlContent="" // No YAML content
      />
    );

    // Should not start creation without YAML content
    expect(screen.getByText('Preparing to create experience...')).toBeInTheDocument();
  });

  test('displays retry button on error', async () => {
    api.experiencesAPI.create.mockRejectedValue(new Error('API Error'));

    renderWithProviders(<ExperienceCreationStep {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Creation Failed')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });
});
