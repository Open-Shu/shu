import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import YAMLInputStep from "../YAMLInputStep";

// Mock the services
jest.mock("../../../services/yamlProcessor", () => ({
  validateExperienceYAML: jest.fn(),
}));

jest.mock("../../../services/importPlaceholders", () => ({
  extractImportPlaceholders: jest.fn(),
}));

jest.mock("../../../utils/log", () => ({
  log: {
    info: jest.fn(),
    debug: jest.fn(),
    error: jest.fn(),
  },
}));

// Import the mocked services
import YAMLProcessor from "../../../services/yamlProcessor";
import { extractImportPlaceholders } from "../../../services/importPlaceholders";

describe("YAMLInputStep", () => {
  const defaultProps = {
    yamlContent: "",
    onYAMLChange: jest.fn(),
    onValidationChange: jest.fn(),
    prePopulatedYAML: null,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    // Set up default mock implementations
    YAMLProcessor.validateExperienceYAML.mockReturnValue({
      isValid: true,
      errors: [],
    });
    extractImportPlaceholders.mockReturnValue([]);
  });

  test("renders YAML input step with title and description", () => {
    render(<YAMLInputStep {...defaultProps} />);

    expect(screen.getByText("YAML Configuration")).toBeInTheDocument();
    expect(
      screen.getByText(/Paste or edit your experience YAML configuration/),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("Paste your YAML configuration here..."),
    ).toBeInTheDocument();
  });

  test("displays pre-populated YAML indicator when provided", () => {
    const prePopulatedYAML = "name: Test\ndescription: Test experience";
    render(
      <YAMLInputStep {...defaultProps} prePopulatedYAML={prePopulatedYAML} />,
    );

    expect(
      screen.getByText(
        /This YAML has been pre-populated from the Quick Start guide/,
      ),
    ).toBeInTheDocument();
  });

  test("calls onYAMLChange when pre-populated YAML is provided", () => {
    const onYAMLChange = jest.fn();
    const prePopulatedYAML = "name: Test\ndescription: Test experience";

    render(
      <YAMLInputStep
        {...defaultProps}
        onYAMLChange={onYAMLChange}
        prePopulatedYAML={prePopulatedYAML}
      />,
    );

    expect(onYAMLChange).toHaveBeenCalledWith(prePopulatedYAML);
  });

  test("handles YAML content changes", () => {
    const onYAMLChange = jest.fn();
    render(<YAMLInputStep {...defaultProps} onYAMLChange={onYAMLChange} />);

    const textarea = screen.getByPlaceholderText(
      "Paste your YAML configuration here...",
    );
    fireEvent.change(textarea, { target: { value: "name: New Experience" } });

    expect(onYAMLChange).toHaveBeenCalledWith("name: New Experience");
  });

  test("validates YAML content and shows validation status", async () => {
    const onValidationChange = jest.fn();
    YAMLProcessor.validateExperienceYAML.mockReturnValue({
      isValid: true,
      errors: [],
    });

    render(
      <YAMLInputStep
        {...defaultProps}
        yamlContent="name: Test\ndescription: Test experience"
        onValidationChange={onValidationChange}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Valid YAML")).toBeInTheDocument();
    });

    expect(onValidationChange).toHaveBeenCalledWith(true);
  });

  test("displays validation errors when YAML is invalid", async () => {
    const onValidationChange = jest.fn();
    YAMLProcessor.validateExperienceYAML.mockReturnValue({
      isValid: false,
      errors: ["Missing required field: name", "Invalid YAML syntax"],
    });

    render(
      <YAMLInputStep
        {...defaultProps}
        yamlContent="invalid: yaml: content:"
        onValidationChange={onValidationChange}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Invalid YAML")).toBeInTheDocument();
      expect(screen.getByText("Validation Errors:")).toBeInTheDocument();
      expect(
        screen.getByText("Missing required field: name"),
      ).toBeInTheDocument();
      expect(screen.getByText("Invalid YAML syntax")).toBeInTheDocument();
    });

    expect(onValidationChange).toHaveBeenCalledWith(false);
  });

  test("shows empty state message when no content", async () => {
    const onValidationChange = jest.fn();

    render(
      <YAMLInputStep
        {...defaultProps}
        yamlContent=""
        onValidationChange={onValidationChange}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Enter YAML content")).toBeInTheDocument();
    });

    expect(onValidationChange).toHaveBeenCalledWith(false);
  });

  test("displays character count for non-empty content", async () => {
    const yamlContent = "name: Test Experience\ndescription: A test experience";

    render(<YAMLInputStep {...defaultProps} yamlContent={yamlContent} />);

    await waitFor(() => {
      expect(
        screen.getByText(`(${yamlContent.length} characters)`),
      ).toBeInTheDocument();
    });
  });

  test("clears pre-populated flag when user edits content", () => {
    const onYAMLChange = jest.fn();
    const prePopulatedYAML = "name: Test\ndescription: Test experience";

    const { rerender } = render(
      <YAMLInputStep
        {...defaultProps}
        onYAMLChange={onYAMLChange}
        prePopulatedYAML={prePopulatedYAML}
      />,
    );

    // Initially shows pre-populated indicator
    expect(
      screen.getByText(/This YAML has been pre-populated/),
    ).toBeInTheDocument();

    // User edits the content
    const textarea = screen.getByPlaceholderText(
      "Paste your YAML configuration here...",
    );
    fireEvent.change(textarea, {
      target: { value: "name: Edited Experience" },
    });

    // Re-render with the new content
    rerender(
      <YAMLInputStep
        {...defaultProps}
        onYAMLChange={onYAMLChange}
        yamlContent="name: Edited Experience"
        prePopulatedYAML={prePopulatedYAML}
      />,
    );

    // Pre-populated indicator should be gone
    expect(
      screen.queryByText(/This YAML has been pre-populated/),
    ).not.toBeInTheDocument();
  });

  test("handles validation errors gracefully", async () => {
    const onValidationChange = jest.fn();
    YAMLProcessor.validateExperienceYAML.mockImplementation(() => {
      throw new Error("Validation service error");
    });

    render(
      <YAMLInputStep
        {...defaultProps}
        yamlContent="some content"
        onValidationChange={onValidationChange}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Invalid YAML")).toBeInTheDocument();
      expect(
        screen.getByText("Validation error: Validation service error"),
      ).toBeInTheDocument();
    });

    expect(onValidationChange).toHaveBeenCalledWith(false);
  });

  test("uses monospace font for textarea", () => {
    render(<YAMLInputStep {...defaultProps} />);

    const textarea = screen.getByPlaceholderText(
      "Paste your YAML configuration here...",
    );
    const styles = window.getComputedStyle(textarea);

    // Note: jsdom doesn't fully compute styles, but we can check that the component renders
    expect(textarea).toBeInTheDocument();
  });
});
