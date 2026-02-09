import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import ImportExperienceWizard from '../ImportExperienceWizard';

// Mock the step components to prevent infinite loops in tests
jest.mock('../import-wizard/YAMLInputStep', () => {
  const mockReact = require('react');
  return function MockYAMLInputStep({ yamlContent, onYAMLChange, onValidationChange, prePopulatedYAML }) {
    // Simulate validation change on mount - but only once
    mockReact.useEffect(() => {
      if (onValidationChange) {
        onValidationChange(yamlContent && yamlContent.trim() !== '');
      }
    }, [yamlContent]); // Removed onValidationChange from dependencies

    return mockReact.createElement(
      'div',
      null,
      mockReact.createElement('h6', null, 'YAML Configuration'),
      mockReact.createElement('p', null, 'Mock YAML Input Step'),
      mockReact.createElement(
        'p',
        null,
        `Current state: ${yamlContent ? `${yamlContent.length} characters` : 'Empty'}`
      ),
      mockReact.createElement('p', null, `Pre-populated: ${prePopulatedYAML ? 'Yes' : 'No'}`),
      mockReact.createElement(
        'button',
        {
          onClick: () => onYAMLChange && onYAMLChange('test yaml content'),
        },
        'Change YAML'
      )
    );
  };
});

jest.mock('../import-wizard/PlaceholderFormStep', () => {
  const mockReact = require('react');
  return function MockPlaceholderFormStep({ placeholders, onValidationChange }) {
    mockReact.useEffect(() => {
      if (onValidationChange) {
        onValidationChange(true);
      }
    }, []); // Empty dependency array

    return mockReact.createElement(
      'div',
      null,
      mockReact.createElement('h6', null, 'Placeholder Form Step'),
      mockReact.createElement('p', null, 'Mock Placeholder Form Step'),
      mockReact.createElement('p', null, `Placeholders found: ${placeholders.length}`)
    );
  };
});

jest.mock('../import-wizard/ExperienceCreationStep', () => {
  const mockReact = require('react');
  return function MockExperienceCreationStep({ isCreating, error, success, experienceId }) {
    return mockReact.createElement(
      'div',
      null,
      mockReact.createElement('h6', null, 'Experience Creation Step'),
      mockReact.createElement('p', null, 'Mock Experience Creation Step'),
      isCreating && mockReact.createElement('p', null, 'Creating...'),
      error && mockReact.createElement('p', null, `Error: ${error}`),
      success && mockReact.createElement('p', null, `Success! Experience ID: ${experienceId}`)
    );
  };
});

// Mock the services
jest.mock('../../services/yamlProcessor', () => ({
  convertToExperiencePayload: jest.fn(() => ({
    name: 'Test Experience',
    description: 'Test',
  })),
  validateExperienceYAML: jest.fn(() => ({ isValid: true, errors: [] })),
}));

jest.mock('../../services/api', () => ({
  experiencesAPI: {
    create: jest.fn(),
  },
  formatError: jest.fn((error) => error.message || 'Unknown error'),
}));

jest.mock('../../utils/log', () => ({
  log: {
    info: jest.fn(),
    debug: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  },
}));

const createTestQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

const renderWithQueryClient = (component) => {
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}>{component}</QueryClientProvider>);
};

describe('ImportExperienceWizard', () => {
  const defaultProps = {
    open: true,
    onClose: jest.fn(),
    onSuccess: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders wizard dialog when open', () => {
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} />);

    expect(screen.getByText('Import Experience')).toBeInTheDocument();
    expect(screen.getByText('YAML Input')).toBeInTheDocument();
    expect(screen.getByText('Configure Values')).toBeInTheDocument();
    expect(screen.getByText('Create Experience')).toBeInTheDocument();
  });

  test('does not render when closed', () => {
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} open={false} />);

    expect(screen.queryByText('Import Experience')).not.toBeInTheDocument();
  });

  test('shows first step content initially', () => {
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} />);

    expect(screen.getByText('YAML Configuration')).toBeInTheDocument();
    expect(screen.getByText('Mock YAML Input Step')).toBeInTheDocument();
  });

  test('displays pre-populated YAML indicator', () => {
    const prePopulatedYAML = 'name: Test\ndescription: Test experience';
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} prePopulatedYAML={prePopulatedYAML} />);

    expect(screen.getByText(/Pre-populated: Yes/)).toBeInTheDocument();
  });

  test('handles close button click', () => {
    const onClose = jest.fn();
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} onClose={onClose} />);

    // The close button is the first button without text content
    const closeButton = screen.getAllByRole('button')[0];
    fireEvent.click(closeButton);

    expect(onClose).toHaveBeenCalled();
  });

  test('handles cancel button click', () => {
    const onClose = jest.fn();
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} onClose={onClose} />);

    const cancelButton = screen.getByRole('button', { name: /cancel/i });
    fireEvent.click(cancelButton);

    expect(onClose).toHaveBeenCalled();
  });

  test('next button is initially disabled', () => {
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} />);

    const nextButton = screen.getByRole('button', { name: /next/i });
    expect(nextButton).toBeDisabled();
  });

  test('back button is disabled on first step', () => {
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} />);

    const backButton = screen.getByRole('button', { name: /back/i });
    expect(backButton).toBeDisabled();
  });

  test('initializes with pre-populated YAML content', () => {
    const prePopulatedYAML = 'name: Morning Briefing\ndescription: Daily briefing';
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} prePopulatedYAML={prePopulatedYAML} />);

    // The YAML content should be passed to the YAMLInputStep
    expect(screen.getByText(/Pre-populated: Yes/)).toBeInTheDocument();
  });

  test('resets state when closed and reopened', async () => {
    const { rerender } = renderWithQueryClient(<ImportExperienceWizard {...defaultProps} open={true} />);

    // Close the wizard
    const queryClient = createTestQueryClient();
    rerender(
      <QueryClientProvider client={queryClient}>
        <ImportExperienceWizard {...defaultProps} open={false} />
      </QueryClientProvider>
    );

    // Reopen the wizard
    rerender(
      <QueryClientProvider client={queryClient}>
        <ImportExperienceWizard {...defaultProps} open={true} />
      </QueryClientProvider>
    );

    // Should be back to first step
    expect(screen.getByText('Mock YAML Input Step')).toBeInTheDocument();
  });

  test('maintains stepper state correctly', () => {
    renderWithQueryClient(<ImportExperienceWizard {...defaultProps} />);

    // Check that stepper shows correct active step
    const stepLabels = screen.getAllByText(/^(YAML Input|Configure Values|Create Experience)$/);

    expect(stepLabels).toHaveLength(3);
  });
});
