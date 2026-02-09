import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { BrowserRouter } from 'react-router-dom';
import ExperienceCreationStep from '../ExperienceCreationStep';

// Mock the API service
jest.mock('../../../services/api', () => ({
  experiencesAPI: {
    create: jest.fn(),
  },
  extractDataFromResponse: jest.fn((response) => response.data),
  formatError: jest.fn((error) => error.message || 'Unknown error'),
}));

// Mock the YAML processor
jest.mock('../../../services/yamlProcessor', () => ({
  convertToExperiencePayload: jest.fn(() => ({
    name: 'Test Experience',
    description: 'Test Description',
    steps: [],
  })),
}));

// Mock the logger
jest.mock('../../../utils/log', () => ({
  log: {
    info: jest.fn(),
    debug: jest.fn(),
    error: jest.fn(),
  },
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
    onCreationComplete: jest.fn(),
    onRetry: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
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
    const { experiencesAPI } = require('../../../services/api');
    experiencesAPI.create.mockRejectedValue(new Error('API Error'));

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
    const { experiencesAPI } = require('../../../services/api');
    experiencesAPI.create.mockRejectedValue(new Error('API Error'));

    renderWithProviders(<ExperienceCreationStep {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Creation Failed')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });
});
