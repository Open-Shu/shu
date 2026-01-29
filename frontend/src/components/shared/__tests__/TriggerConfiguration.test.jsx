import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Mock the RecurringScheduleBuilder component
jest.mock("../RecurringScheduleBuilder", () => {
  return function MockRecurringScheduleBuilder({ value, onChange }) {
    return (
      <div data-testid="recurring-schedule-builder">
        <input
          data-testid="mock-recurring-input"
          value={value.cron || ""}
          onChange={(e) => onChange({ ...value, cron: e.target.value })}
        />
      </div>
    );
  };
});

// Mock the TimezoneSelector component
jest.mock("../TimezoneSelector", () => {
  return function MockTimezoneSelector({ value, onChange }) {
    return (
      <div data-testid="timezone-selector">
        <input
          data-testid="mock-timezone-input"
          value={value || ""}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  };
});

import TriggerConfiguration from "../TriggerConfiguration";

describe("TriggerConfiguration", () => {
  const defaultProps = {
    triggerType: "manual",
    triggerConfig: {},
    onTriggerTypeChange: jest.fn(),
    onTriggerConfigChange: jest.fn(),
    validationErrors: {},
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("renders trigger type selector", () => {
    render(<TriggerConfiguration {...defaultProps} />);

    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByText("Manual")).toBeInTheDocument();
    expect(screen.getAllByText("Trigger Type")).toHaveLength(2); // Label and legend
  });

  test("shows manual trigger info", () => {
    render(<TriggerConfiguration {...defaultProps} />);

    expect(
      screen.getByText(
        "Manual trigger selected - no additional configuration needed.",
      ),
    ).toBeInTheDocument();
  });

  test("shows scheduled trigger configuration", () => {
    render(<TriggerConfiguration {...defaultProps} triggerType="scheduled" />);

    expect(screen.getByLabelText("Scheduled Date/Time")).toBeInTheDocument();
    expect(screen.getByText("Timezone")).toBeInTheDocument();
    expect(screen.getByTestId("timezone-selector")).toBeInTheDocument();
  });

  test("shows recurring trigger configuration", () => {
    render(<TriggerConfiguration {...defaultProps} triggerType="cron" />);

    expect(
      screen.getByTestId("recurring-schedule-builder"),
    ).toBeInTheDocument();
  });

  test("calls onTriggerTypeChange when trigger type changes", () => {
    const { container } = render(<TriggerConfiguration {...defaultProps} />);

    const select = container.querySelector(".MuiSelect-nativeInput");
    fireEvent.change(select, { target: { value: "scheduled" } });

    expect(defaultProps.onTriggerTypeChange).toHaveBeenCalledWith("scheduled");
  });

  test("calls onTriggerConfigChange when scheduled date changes", () => {
    render(<TriggerConfiguration {...defaultProps} triggerType="scheduled" />);

    const dateInput = screen.getByLabelText("Scheduled Date/Time");
    fireEvent.change(dateInput, { target: { value: "2024-01-01T10:00" } });

    expect(defaultProps.onTriggerConfigChange).toHaveBeenCalledWith({
      scheduled_at: "2024-01-01T10:00",
    });
  });

  test("calls onTriggerConfigChange when timezone changes for scheduled trigger", () => {
    render(<TriggerConfiguration {...defaultProps} triggerType="scheduled" />);

    const timezoneInput = screen.getByTestId("mock-timezone-input");
    fireEvent.change(timezoneInput, { target: { value: "America/New_York" } });

    expect(defaultProps.onTriggerConfigChange).toHaveBeenCalledWith({
      timezone: "America/New_York",
    });
  });

  test("shows validation errors", () => {
    render(
      <TriggerConfiguration
        {...defaultProps}
        validationErrors={{ trigger_type: "Required field" }}
      />,
    );

    expect(screen.getByText("Required field")).toBeInTheDocument();
  });

  test("shows required indicator when required prop is true", () => {
    render(<TriggerConfiguration {...defaultProps} required />);

    expect(screen.getAllByText("Trigger Type *")).toHaveLength(2); // Label and legend
  });

  test("resets config when trigger type changes", () => {
    const { container } = render(
      <TriggerConfiguration
        {...defaultProps}
        triggerConfig={{ scheduled_at: "2024-01-01T10:00" }}
      />,
    );

    const select = container.querySelector(".MuiSelect-nativeInput");
    fireEvent.change(select, { target: { value: "cron" } });

    expect(defaultProps.onTriggerConfigChange).toHaveBeenCalledWith({});
  });

  test("displays existing scheduled configuration", () => {
    render(
      <TriggerConfiguration
        {...defaultProps}
        triggerType="scheduled"
        triggerConfig={{
          scheduled_at: "2024-01-01T10:00",
          timezone: "America/New_York",
        }}
      />,
    );

    const dateInput = screen.getByLabelText("Scheduled Date/Time");
    const timezoneInput = screen.getByTestId("mock-timezone-input");

    expect(dateInput).toHaveValue("2024-01-01T10:00");
    expect(timezoneInput).toHaveValue("America/New_York");
  });

  test("displays existing cron configuration (backward compatibility)", () => {
    render(
      <TriggerConfiguration
        {...defaultProps}
        triggerType="cron"
        triggerConfig={{
          cron: "0 9 * * 1-5",
          timezone: "America/New_York",
        }}
      />,
    );

    const recurringBuilder = screen.getByTestId("recurring-schedule-builder");
    const cronInput = screen.getByTestId("mock-recurring-input");

    expect(recurringBuilder).toBeInTheDocument();
    expect(cronInput).toHaveValue("0 9 * * 1-5");
  });

  test("handles cron configuration without timezone (backward compatibility)", () => {
    render(
      <TriggerConfiguration
        {...defaultProps}
        triggerType="cron"
        triggerConfig={{
          cron: "0 9 * * *",
        }}
      />,
    );

    const recurringBuilder = screen.getByTestId("recurring-schedule-builder");

    expect(recurringBuilder).toBeInTheDocument();
  });

  test("calls onTriggerConfigChange when cron expression changes", () => {
    render(
      <TriggerConfiguration
        {...defaultProps}
        triggerType="cron"
        triggerConfig={{
          cron: "0 9 * * *",
          timezone: "America/New_York",
        }}
      />,
    );

    const cronInput = screen.getByTestId("mock-recurring-input");
    fireEvent.change(cronInput, { target: { value: "0 10 * * *" } });

    expect(defaultProps.onTriggerConfigChange).toHaveBeenCalledWith({
      cron: "0 10 * * *",
      timezone: "America/New_York",
    });
  });
});
