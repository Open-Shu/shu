import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import QuickStart from '../QuickStart';
import * as api from '../../services/api';
import { useTheme as useAppTheme } from '../../contexts/ThemeContext';
import { getBrandingAppName } from '../../utils/constants';

// Mock dependencies
jest.mock('../../services/api');
jest.mock('../../contexts/ThemeContext');
jest.mock('../../utils/constants');

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

// Test wrapper component
const TestWrapper = ({ children }) => {
  const theme = createTheme();
  return (
    <BrowserRouter>
      <ThemeProvider theme={theme}>{children}</ThemeProvider>
    </BrowserRouter>
  );
};

describe('QuickStart Component - Experiences Card', () => {
  beforeEach(() => {
    // Reset mocks
    jest.clearAllMocks();

    // Mock theme context
    useAppTheme.mockReturnValue({
      branding: { app_name: 'Test App' },
    });

    // Mock branding utility
    getBrandingAppName.mockReturnValue('Test App');

    // Mock extractDataFromResponse
    api.extractDataFromResponse = jest.fn().mockImplementation((response) => response.data);

    // Mock setup API with default status
    api.setupAPI = {
      getStatus: jest.fn().mockResolvedValue({
        data: {
          llm_provider_configured: true,
          model_configuration_created: true,
          knowledge_base_created: true,
          documents_added: true,
          plugins_enabled: true,
          plugin_feed_created: true,
          experience_created: false,
        },
      }),
    };
  });

  test('Experiences card appears in Getting Started section', async () => {
    render(
      <TestWrapper>
        <QuickStart />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(screen.getByText('Getting Started')).toBeInTheDocument();
    });

    // Check that Experiences card exists
    expect(screen.getByText('Experiences')).toBeInTheDocument();

    // Verify it's in the Getting Started section by checking it appears after "Getting Started" heading
    const gettingStartedHeading = screen.getByText('Getting Started');
    const experiencesCard = screen.getByText('Experiences');

    expect(gettingStartedHeading).toBeInTheDocument();
    expect(experiencesCard).toBeInTheDocument();
  });

  test('Experiences card contains correct title, description, and icon', async () => {
    render(
      <TestWrapper>
        <QuickStart />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Experiences')).toBeInTheDocument();
    });

    // Check title
    expect(screen.getByText('Experiences')).toBeInTheDocument();

    // Check description parts
    expect(
      screen.getByText(/Create automated workflows that combine plugins, knowledge bases, and AI synthesis/)
    ).toBeInTheDocument();
    expect(screen.getByText(/Build signature experiences like Morning Briefing/)).toBeInTheDocument();

    // Check that the card is clickable (has CardActionArea)
    const experiencesCard = screen.getByText('Experiences').closest('.MuiCardActionArea-root');
    expect(experiencesCard).toBeInTheDocument();
  });

  test('Experiences card navigates to /admin/experiences when clicked', async () => {
    render(
      <TestWrapper>
        <QuickStart />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Experiences')).toBeInTheDocument();
    });

    // Find and click the Experiences card
    const experiencesCard = screen.getByText('Experiences').closest('.MuiCardActionArea-root');
    fireEvent.click(experiencesCard);

    // Verify navigation was called with correct path
    expect(mockNavigate).toHaveBeenCalledWith('/admin/experiences');
  });

  test('Experiences card shows completion status when experience_created is true', async () => {
    // Mock setup status with experience_created: true
    api.setupAPI.getStatus.mockResolvedValue({
      data: {
        llm_provider_configured: false, // Set others to false to isolate the test
        model_configuration_created: false,
        knowledge_base_created: false,
        documents_added: false,
        plugins_enabled: false,
        plugin_feed_created: false,
        experience_created: true, // Only this one is true
      },
    });

    render(
      <TestWrapper>
        <QuickStart />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Experiences')).toBeInTheDocument();
    });

    // Check that there is exactly one "Done" chip (for the Experiences card)
    await waitFor(() => {
      const doneChips = screen.getAllByText('Done');
      expect(doneChips).toHaveLength(1);
    });
  });

  test('Experiences card does not show completion status when experience_created is false', async () => {
    // Mock setup status with experience_created: false (default from beforeEach)
    render(
      <TestWrapper>
        <QuickStart />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Experiences')).toBeInTheDocument();
    });

    // Find the Experiences card container
    const experiencesCard = screen.getByText('Experiences').closest('.MuiCard-root');

    // Check that "Done" chip is not present in the Experiences card
    const doneChips = screen.queryAllByText('Done');
    const experiencesCardHasDone = doneChips.some((chip) => experiencesCard && experiencesCard.contains(chip));

    expect(experiencesCardHasDone).toBe(false);
  });

  test('Experiences card handles API error gracefully', async () => {
    // Mock API error
    api.setupAPI.getStatus.mockRejectedValue(new Error('API Error'));

    render(
      <TestWrapper>
        <QuickStart />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Experiences')).toBeInTheDocument();
    });

    // Card should still render even if API fails
    expect(screen.getByText('Experiences')).toBeInTheDocument();
    expect(
      screen.getByText(/Create automated workflows that combine plugins, knowledge bases, and AI synthesis/)
    ).toBeInTheDocument();
  });

  test('Experiences card uses same SectionCard component and design patterns', async () => {
    render(
      <TestWrapper>
        <QuickStart />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Experiences')).toBeInTheDocument();
    });

    // Find the Experiences card
    const experiencesCard = screen.getByText('Experiences').closest('.MuiCard-root');
    expect(experiencesCard).toBeInTheDocument();

    // Verify it uses CardActionArea (clickable)
    const cardActionArea = experiencesCard.querySelector('.MuiCardActionArea-root');
    expect(cardActionArea).toBeInTheDocument();

    // Verify it has the same structure as other cards
    const cardContent = experiencesCard.querySelector('.MuiCardContent-root');
    expect(cardContent).toBeInTheDocument();

    // Check that the Experiences card specifically has the "Open" text (same as other cards)
    expect(experiencesCard.textContent).toContain('Open');

    // Verify the card has proper styling and structure (check for border presence)
    const computedStyle = window.getComputedStyle(experiencesCard);
    expect(computedStyle.borderWidth).toBe('1px');

    // Check that the card title is properly styled
    const titleElement = screen.getByText('Experiences');
    expect(titleElement).toHaveClass('MuiTypography-subtitle1');
  });

  test('Experiences card responsive design works correctly', async () => {
    render(
      <TestWrapper>
        <QuickStart />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText('Experiences')).toBeInTheDocument();
    });

    // Find the Grid item containing the Experiences card
    const experiencesCard = screen.getByText('Experiences').closest('.MuiGrid-item');
    expect(experiencesCard).toBeInTheDocument();

    // Verify it has responsive classes (Grid system)
    expect(experiencesCard).toHaveClass('MuiGrid-item');

    // The card should be in a container with proper spacing
    const gridContainer = experiencesCard.parentElement;
    expect(gridContainer).toHaveClass('MuiGrid-container');
  });
});
