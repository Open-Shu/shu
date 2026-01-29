import React from "react";
import { render, screen, fireEvent, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import ConversationSidebar from "../ConversationSidebar";

// Mock react-router-dom
const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

// Mock MarkdownRenderer
jest.mock("../../../shared/MarkdownRenderer", () => {
  return function MockMarkdownRenderer({ content }) {
    return <div data-testid="markdown-renderer">{content}</div>;
  };
});

// Test wrapper component
const TestWrapper = ({ children }) => {
  const theme = createTheme();
  return (
    <BrowserRouter>
      <ThemeProvider theme={theme}>{children}</ThemeProvider>
    </BrowserRouter>
  );
};

// Mock branding and chat styles
const mockBranding = {
  appDisplayName: "Test App",
  logoUrl: "/test-logo.png",
  primaryMain: "#1976d2",
};

const mockChatStyles = {
  conversationBorderColor: "rgba(0, 0, 0, 0.12)",
  conversationSelectedBg: "rgba(25, 118, 210, 0.08)",
  conversationSelectedText: "#1976d2",
  conversationHoverBg: "rgba(0, 0, 0, 0.04)",
};

// Mock conversations
const createMockConversation = (
  id,
  title,
  isFavorite = false,
  updatedAt = new Date(),
) => ({
  id,
  title,
  is_favorite: isFavorite,
  is_active: true,
  updated_at: updatedAt.toISOString(),
  model_configuration: {
    name: "Test Model",
    knowledge_bases: [],
  },
  meta: {},
});

describe("ConversationSidebar - Favorite Functionality", () => {
  let defaultProps;

  beforeEach(() => {
    jest.clearAllMocks();

    defaultProps = {
      conversations: [],
      loadingConversations: false,
      selectedConversationId: null,
      onSelectConversation: jest.fn(),
      onCreateConversation: jest.fn(),
      createConversationDisabled: false,
      showNoModelsNote: false,
      onRenameConversation: jest.fn(),
      onDeleteConversation: jest.fn(),
      onToggleFavorite: jest.fn(),
      branding: mockBranding,
      chatStyles: mockChatStyles,
      searchValue: "",
      onSearchChange: jest.fn(),
      searchFeedback: null,
      isMobile: false,
    };
  });

  describe("Favorite Button Rendering", () => {
    test("renders favorite button for each conversation", () => {
      const conversations = [
        createMockConversation("1", "Conversation 1", false),
        createMockConversation("2", "Conversation 2", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      // Find all favorite buttons by aria-label
      const favoriteButtons = screen.getAllByLabelText(
        /Add to favorites|Remove from favorites/,
      );
      expect(favoriteButtons).toHaveLength(2);
    });

    test("renders StarBorderIcon for non-favorited conversations", () => {
      const conversations = [
        createMockConversation("1", "Non-Favorite Conversation", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Add to favorites");
      expect(favoriteButton).toBeInTheDocument();

      // Check that StarBorderIcon is rendered (unfilled star)
      const starBorderIcon = favoriteButton.querySelector(
        'svg[data-testid="StarBorderIcon"]',
      );
      expect(starBorderIcon).toBeInTheDocument();
    });

    test("renders StarIcon for favorited conversations", () => {
      const conversations = [
        createMockConversation("1", "Favorite Conversation", true),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Remove from favorites");
      expect(favoriteButton).toBeInTheDocument();

      // Check that StarIcon is rendered (filled star)
      const starIcon = favoriteButton.querySelector(
        'svg[data-testid="StarIcon"]',
      );
      expect(starIcon).toBeInTheDocument();
    });

    test("displays star indicator in conversation title for favorited conversations", () => {
      const conversations = [
        createMockConversation("1", "Favorite Conversation", true),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      // Find the conversation list item
      const conversationItem = screen
        .getByText("Favorite Conversation")
        .closest(".MuiListItemButton-root");

      // Check that there's a star icon in the title area
      const titleStarIcon = within(conversationItem).getAllByTestId("StarIcon");
      expect(titleStarIcon.length).toBeGreaterThan(0);
    });

    test("does not display star indicator in title for non-favorited conversations", () => {
      const conversations = [
        createMockConversation("1", "Non-Favorite Conversation", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      // Find the conversation list item
      const conversationItem = screen
        .getByText("Non-Favorite Conversation")
        .closest(".MuiListItemButton-root");

      // Check that there's no star icon in the title area (only in the button)
      const titleArea = within(conversationItem).getByText(
        "Non-Favorite Conversation",
      ).parentElement;
      const starIconsInTitle = within(titleArea).queryAllByTestId("StarIcon");
      expect(starIconsInTitle).toHaveLength(0);
    });

    test("favorite button is hidden by default and shown on hover", () => {
      const conversations = [
        createMockConversation("1", "Conversation 1", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Add to favorites");

      // Check that button has opacity: 0 initially (hidden)
      expect(favoriteButton).toHaveStyle({ opacity: "0" });

      // Button should have the conversation-action-button class
      expect(favoriteButton).toHaveClass("conversation-action-button");
    });

    test("favorite button is visible on mobile", () => {
      const conversations = [
        createMockConversation("1", "Conversation 1", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
            isMobile={true}
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Add to favorites");

      // On mobile, button should have opacity: 0.7
      expect(favoriteButton).toHaveStyle({ opacity: "0.7" });
    });
  });

  describe("Favorite Toggle Interaction", () => {
    test("calls onToggleFavorite when favorite button is clicked", () => {
      const conversation = createMockConversation(
        "1",
        "Test Conversation",
        false,
      );
      const conversations = [conversation];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Add to favorites");
      fireEvent.click(favoriteButton);

      expect(defaultProps.onToggleFavorite).toHaveBeenCalledTimes(1);
      expect(defaultProps.onToggleFavorite).toHaveBeenCalledWith(conversation);
    });

    test("calls onToggleFavorite with correct conversation when unfavoriting", () => {
      const conversation = createMockConversation(
        "1",
        "Favorite Conversation",
        true,
      );
      const conversations = [conversation];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Remove from favorites");
      fireEvent.click(favoriteButton);

      expect(defaultProps.onToggleFavorite).toHaveBeenCalledTimes(1);
      expect(defaultProps.onToggleFavorite).toHaveBeenCalledWith(conversation);
    });

    test("does not call onSelectConversation when favorite button is clicked", () => {
      const conversations = [
        createMockConversation("1", "Test Conversation", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Add to favorites");
      fireEvent.click(favoriteButton);

      // Should not select the conversation when clicking favorite button
      expect(defaultProps.onSelectConversation).not.toHaveBeenCalled();
      expect(defaultProps.onToggleFavorite).toHaveBeenCalledTimes(1);
    });

    test("handles onToggleFavorite being undefined gracefully", () => {
      const conversations = [
        createMockConversation("1", "Test Conversation", false),
      ];

      const propsWithoutToggle = {
        ...defaultProps,
        onToggleFavorite: undefined,
      };

      render(
        <TestWrapper>
          <ConversationSidebar
            {...propsWithoutToggle}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Add to favorites");

      // Should not throw error when clicking
      expect(() => fireEvent.click(favoriteButton)).not.toThrow();
    });

    test("clicking favorite button stops event propagation", () => {
      const conversations = [
        createMockConversation("1", "Test Conversation", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Add to favorites");
      fireEvent.click(favoriteButton);

      // Verify that onSelectConversation was not called (event propagation stopped)
      expect(defaultProps.onSelectConversation).not.toHaveBeenCalled();
      expect(defaultProps.onToggleFavorite).toHaveBeenCalledTimes(1);
    });
  });

  describe("Conversation List Sorting", () => {
    test("displays conversations in the order provided (pre-sorted by parent)", () => {
      // The component displays conversations in the order they're provided
      // Sorting happens in the parent component (ModernChat.js)
      const conversations = [
        createMockConversation(
          "2",
          "Favorite 1",
          true,
          new Date("2024-01-15T10:00:00Z"),
        ),
        createMockConversation(
          "4",
          "Favorite 2",
          true,
          new Date("2024-01-15T09:00:00Z"),
        ),
        createMockConversation(
          "3",
          "Non-Favorite 2",
          false,
          new Date("2024-01-15T12:00:00Z"),
        ),
        createMockConversation(
          "1",
          "Non-Favorite 1",
          false,
          new Date("2024-01-15T11:00:00Z"),
        ),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      // Get all conversation list items in order
      const listItems = screen
        .getAllByRole("button")
        .filter((button) =>
          button.classList.contains("MuiListItemButton-root"),
        );

      // Should display in the order provided
      expect(listItems[0].textContent).toContain("Favorite 1");
      expect(listItems[1].textContent).toContain("Favorite 2");
      expect(listItems[2].textContent).toContain("Non-Favorite 2");
      expect(listItems[3].textContent).toContain("Non-Favorite 1");
    });

    test("displays favorited conversations with star indicator", () => {
      const conversations = [
        createMockConversation("1", "Favorite Conv", true),
        createMockConversation("2", "Regular Conv", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      // Find the favorite conversation
      const favoriteItem = screen
        .getByText("Favorite Conv")
        .closest(".MuiListItemButton-root");
      const regularItem = screen
        .getByText("Regular Conv")
        .closest(".MuiListItemButton-root");

      // Favorite should have star indicator in title
      const favoriteStars = within(favoriteItem).getAllByTestId("StarIcon");
      expect(favoriteStars.length).toBeGreaterThan(0);

      // Regular should not have star indicator in title
      const regularTitleArea =
        within(regularItem).getByText("Regular Conv").parentElement;
      const regularStarsInTitle =
        within(regularTitleArea).queryAllByTestId("StarIcon");
      expect(regularStarsInTitle).toHaveLength(0);
    });

    test("renders all conversations regardless of favorite status", () => {
      const conversations = [
        createMockConversation("1", "Conv 1", true),
        createMockConversation("2", "Conv 2", false),
        createMockConversation("3", "Conv 3", true),
        createMockConversation("4", "Conv 4", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      // All conversations should be rendered
      expect(screen.getByText("Conv 1")).toBeInTheDocument();
      expect(screen.getByText("Conv 2")).toBeInTheDocument();
      expect(screen.getByText("Conv 3")).toBeInTheDocument();
      expect(screen.getByText("Conv 4")).toBeInTheDocument();
    });

    test("preserves conversation order when provided", () => {
      // Test that the component doesn't re-sort conversations
      const conversations = [
        createMockConversation(
          "3",
          "Third",
          false,
          new Date("2024-01-15T10:00:00Z"),
        ),
        createMockConversation(
          "1",
          "First",
          true,
          new Date("2024-01-15T12:00:00Z"),
        ),
        createMockConversation(
          "2",
          "Second",
          false,
          new Date("2024-01-15T11:00:00Z"),
        ),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      const listItems = screen
        .getAllByRole("button")
        .filter((button) =>
          button.classList.contains("MuiListItemButton-root"),
        );

      // Should maintain the exact order provided
      expect(listItems[0].textContent).toContain("Third");
      expect(listItems[1].textContent).toContain("First");
      expect(listItems[2].textContent).toContain("Second");
    });

    test("handles empty conversation list", () => {
      render(
        <TestWrapper>
          <ConversationSidebar {...defaultProps} conversations={[]} />
        </TestWrapper>,
      );

      // Should not throw error and should render without conversations
      const conversationButtons = screen.queryAllByRole("button", {
        name: /Conversation/i,
      });
      expect(conversationButtons).toHaveLength(0);
    });

    test("handles single conversation", () => {
      const conversations = [
        createMockConversation("1", "Single Conversation", true),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      expect(screen.getByText("Single Conversation")).toBeInTheDocument();
    });
  });

  describe("Integration Tests", () => {
    test("favorite button works correctly with selected conversation", () => {
      const conversation = createMockConversation(
        "1",
        "Selected Conversation",
        false,
      );
      const conversations = [conversation];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
            selectedConversationId="1"
          />
        </TestWrapper>,
      );

      const favoriteButton = screen.getByLabelText("Add to favorites");
      fireEvent.click(favoriteButton);

      expect(defaultProps.onToggleFavorite).toHaveBeenCalledWith(conversation);
    });

    test("renders multiple conversations with mixed favorite states correctly", () => {
      const conversations = [
        createMockConversation("1", "Favorite 1", true),
        createMockConversation("2", "Non-Favorite 1", false),
        createMockConversation("3", "Favorite 2", true),
        createMockConversation("4", "Non-Favorite 2", false),
      ];

      render(
        <TestWrapper>
          <ConversationSidebar
            {...defaultProps}
            conversations={conversations}
          />
        </TestWrapper>,
      );

      // Check that all conversations are rendered
      expect(screen.getByText("Favorite 1")).toBeInTheDocument();
      expect(screen.getByText("Non-Favorite 1")).toBeInTheDocument();
      expect(screen.getByText("Favorite 2")).toBeInTheDocument();
      expect(screen.getByText("Non-Favorite 2")).toBeInTheDocument();

      // Check that favorite buttons are rendered correctly
      const addToFavoritesButtons =
        screen.getAllByLabelText("Add to favorites");
      const removeFromFavoritesButtons = screen.getAllByLabelText(
        "Remove from favorites",
      );

      expect(addToFavoritesButtons).toHaveLength(2);
      expect(removeFromFavoritesButtons).toHaveLength(2);
    });
  });
});
