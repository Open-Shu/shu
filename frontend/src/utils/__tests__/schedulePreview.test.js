import {
  describe as describeSchedule,
  getNextExecutions,
  formatExecution,
  getSchedulePreview,
} from "../schedulePreview";

describe("schedulePreview", () => {
  describe("describe", () => {
    it("generates human-readable description for daily schedule", () => {
      const result = describeSchedule("0 9 * * *", "America/New_York");
      expect(result).toContain("9:00 AM");
      expect(result).toContain("EST");
    });

    it("generates human-readable description for weekday schedule", () => {
      const result = describeSchedule("0 9 * * 1-5", "America/New_York");
      expect(result).toContain("Monday through Friday");
      expect(result).toContain("9:00 AM");
    });

    it("generates human-readable description for weekly schedule", () => {
      const result = describeSchedule("0 14 * * 1", "Europe/London");
      expect(result).toContain("Monday");
      expect(result).toContain("2:00 PM");
    });

    it("includes timezone abbreviation in description", () => {
      const result = describeSchedule("0 9 * * *", "America/Los_Angeles");
      expect(result).toMatch(/PST|PDT/);
    });

    it("throws error for invalid cron expression", () => {
      expect(() => describeSchedule("invalid", "America/New_York")).toThrow();
    });

    it("throws error for empty cron expression", () => {
      expect(() => describeSchedule("", "America/New_York")).toThrow(
        "Invalid cron expression",
      );
    });

    it("throws error for null cron expression", () => {
      expect(() => describeSchedule(null, "America/New_York")).toThrow(
        "Invalid cron expression",
      );
    });

    it("throws error for invalid timezone", () => {
      expect(() => describeSchedule("0 9 * * *", "")).toThrow(
        "Invalid timezone",
      );
    });

    it("throws error for null timezone", () => {
      expect(() => describeSchedule("0 9 * * *", null)).toThrow(
        "Invalid timezone",
      );
    });
  });

  describe("getNextExecutions", () => {
    it("calculates next 5 execution times by default", () => {
      const executions = getNextExecutions("0 9 * * *", "America/New_York");
      expect(executions).toHaveLength(5);
      expect(executions[0]).toBeInstanceOf(Date);
    });

    it("calculates specified number of execution times", () => {
      const executions = getNextExecutions("0 9 * * *", "America/New_York", 3);
      expect(executions).toHaveLength(3);
    });

    it("returns execution times in chronological order", () => {
      const executions = getNextExecutions("0 9 * * *", "America/New_York", 3);
      expect(executions[0].getTime()).toBeLessThan(executions[1].getTime());
      expect(executions[1].getTime()).toBeLessThan(executions[2].getTime());
    });

    it("handles weekly schedules correctly", () => {
      const executions = getNextExecutions("0 9 * * 1", "America/New_York", 2);
      expect(executions).toHaveLength(2);
      // Both should be Mondays
      expect(executions[0].getDay()).toBe(1);
      expect(executions[1].getDay()).toBe(1);
    });

    it("handles monthly schedules correctly", () => {
      const executions = getNextExecutions("0 9 1 * *", "America/New_York", 3);
      expect(executions).toHaveLength(3);
      // All should be on the 1st of the month
      executions.forEach((date) => {
        const dayOfMonth = new Date(
          date.toLocaleString("en-US", { timeZone: "America/New_York" }),
        ).getDate();
        expect(dayOfMonth).toBe(1);
      });
    });

    it("respects timezone when calculating executions", () => {
      const executionsNY = getNextExecutions(
        "0 9 * * *",
        "America/New_York",
        1,
      );
      const executionsLA = getNextExecutions(
        "0 9 * * *",
        "America/Los_Angeles",
        1,
      );

      // Same cron time but different timezones should give different UTC times
      expect(executionsNY[0].getTime()).not.toBe(executionsLA[0].getTime());
    });

    it("throws error for invalid cron expression", () => {
      expect(() => getNextExecutions("invalid", "America/New_York")).toThrow();
    });

    it("throws error for empty cron expression", () => {
      expect(() => getNextExecutions("", "America/New_York")).toThrow(
        "Invalid cron expression",
      );
    });

    it("throws error for invalid timezone", () => {
      expect(() =>
        getNextExecutions("0 9 * * *", "Invalid/Timezone"),
      ).toThrow();
    });

    it("throws error for count less than 1", () => {
      expect(() =>
        getNextExecutions("0 9 * * *", "America/New_York", 0),
      ).toThrow("Count must be an integer between 1 and 10");
    });

    it("throws error for count greater than 10", () => {
      expect(() =>
        getNextExecutions("0 9 * * *", "America/New_York", 11),
      ).toThrow("Count must be an integer between 1 and 10");
    });

    it("throws error for non-integer count", () => {
      expect(() =>
        getNextExecutions("0 9 * * *", "America/New_York", 3.5),
      ).toThrow("Count must be an integer between 1 and 10");
    });
  });

  describe("formatExecution", () => {
    it("formats execution time with full date and timezone", () => {
      const date = new Date("2026-01-14T14:00:00Z");
      const result = formatExecution(date, "America/New_York");

      expect(result).toContain("January");
      expect(result).toContain("2026");
      expect(result).toMatch(/EST|EDT/);
    });

    it("formats execution time with day of week", () => {
      const date = new Date("2026-01-14T14:00:00Z");
      const result = formatExecution(date, "America/New_York");

      expect(result).toMatch(
        /Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday/,
      );
    });

    it("formats time in 12-hour format with AM/PM", () => {
      const date = new Date("2026-01-14T14:00:00Z");
      const result = formatExecution(date, "America/New_York");

      expect(result).toMatch(/\d{1,2}:\d{2} (AM|PM)/);
    });

    it("respects timezone when formatting", () => {
      const date = new Date("2026-01-14T14:00:00Z");
      const resultNY = formatExecution(date, "America/New_York");
      const resultLA = formatExecution(date, "America/Los_Angeles");

      // Same UTC time should show different local times
      expect(resultNY).not.toBe(resultLA);
    });

    it("throws error for invalid date", () => {
      expect(() =>
        formatExecution(new Date("invalid"), "America/New_York"),
      ).toThrow("Invalid date");
    });

    it("throws error for null date", () => {
      expect(() => formatExecution(null, "America/New_York")).toThrow(
        "Invalid date",
      );
    });

    it("throws error for non-Date object", () => {
      expect(() => formatExecution("2026-01-14", "America/New_York")).toThrow(
        "Invalid date",
      );
    });

    it("throws error for invalid timezone", () => {
      const date = new Date("2026-01-14T14:00:00Z");
      expect(() => formatExecution(date, "Invalid/Timezone")).toThrow();
    });

    it("throws error for empty timezone", () => {
      const date = new Date("2026-01-14T14:00:00Z");
      expect(() => formatExecution(date, "")).toThrow("Invalid timezone");
    });
  });

  describe("getSchedulePreview", () => {
    it("returns complete preview with description and executions", () => {
      const preview = getSchedulePreview("0 9 * * *", "America/New_York", 3);

      expect(preview).toHaveProperty("description");
      expect(preview).toHaveProperty("nextExecutions");
      expect(preview).toHaveProperty("executionDates");
      expect(preview.nextExecutions).toHaveLength(3);
      expect(preview.executionDates).toHaveLength(3);
    });

    it("description contains human-readable schedule", () => {
      const preview = getSchedulePreview("0 9 * * 1-5", "America/New_York");

      expect(preview.description).toContain("Monday through Friday");
      expect(preview.description).toContain("9:00 AM");
    });

    it("nextExecutions are formatted strings", () => {
      const preview = getSchedulePreview("0 9 * * *", "America/New_York", 2);

      preview.nextExecutions.forEach((execution) => {
        expect(typeof execution).toBe("string");
        expect(execution).toContain("at");
        expect(execution).toMatch(/EST|EDT/);
      });
    });

    it("executionDates are Date objects", () => {
      const preview = getSchedulePreview("0 9 * * *", "America/New_York", 2);

      preview.executionDates.forEach((date) => {
        expect(date).toBeInstanceOf(Date);
      });
    });

    it("uses default count of 5 when not specified", () => {
      const preview = getSchedulePreview("0 9 * * *", "America/New_York");

      expect(preview.nextExecutions).toHaveLength(5);
      expect(preview.executionDates).toHaveLength(5);
    });

    it("respects custom count parameter", () => {
      const preview = getSchedulePreview("0 9 * * *", "America/New_York", 3);

      expect(preview.nextExecutions).toHaveLength(3);
      expect(preview.executionDates).toHaveLength(3);
    });

    it("throws error for invalid cron expression", () => {
      expect(() => getSchedulePreview("invalid", "America/New_York")).toThrow();
    });

    it("throws error for empty cron expression", () => {
      expect(() => getSchedulePreview("", "America/New_York")).toThrow(
        "Invalid cron expression",
      );
    });

    it("throws error for invalid timezone", () => {
      expect(() => getSchedulePreview("0 9 * * *", "")).toThrow(
        "Invalid timezone",
      );
    });

    it("handles complex cron expressions", () => {
      // Every 15 minutes during business hours on weekdays
      const preview = getSchedulePreview(
        "*/15 9-17 * * 1-5",
        "America/New_York",
        3,
      );

      expect(preview.description).toBeTruthy();
      expect(preview.nextExecutions).toHaveLength(3);
    });

    it("handles monthly schedules on specific days", () => {
      // First day of every month at 9 AM
      const preview = getSchedulePreview("0 9 1 * *", "America/New_York", 3);

      expect(preview.description).toContain("day 1 of the month");
      expect(preview.nextExecutions).toHaveLength(3);
    });
  });

  describe("edge cases", () => {
    it("handles leap year dates correctly", () => {
      // February 29th at 9 AM (only on leap years)
      const preview = getSchedulePreview("0 9 29 2 *", "America/New_York", 2);

      expect(preview.nextExecutions.length).toBeGreaterThan(0);
      preview.executionDates.forEach((date) => {
        const month = date.getMonth();
        const day = date.getDate();
        expect(month).toBe(1); // February (0-indexed)
        expect(day).toBe(29);
      });
    });

    it("handles end of month correctly", () => {
      // Last day of month at 9 AM
      const preview = getSchedulePreview("0 9 31 * *", "America/New_York", 3);

      expect(preview.nextExecutions.length).toBeGreaterThan(0);
    });

    it("handles midnight correctly", () => {
      const preview = getSchedulePreview("0 0 * * *", "America/New_York", 2);

      expect(preview.description).toContain("12:00 AM");
      preview.nextExecutions.forEach((execution) => {
        expect(execution).toContain("12:00 AM");
      });
    });

    it("handles noon correctly", () => {
      const preview = getSchedulePreview("0 12 * * *", "America/New_York", 2);

      expect(preview.description).toContain("12:00 PM");
      preview.nextExecutions.forEach((execution) => {
        expect(execution).toContain("12:00 PM");
      });
    });

    it("handles different timezones correctly", () => {
      const timezones = [
        "America/New_York",
        "America/Los_Angeles",
        "Europe/London",
        "Asia/Tokyo",
        "Australia/Sydney",
      ];

      timezones.forEach((tz) => {
        const preview = getSchedulePreview("0 9 * * *", tz, 2);
        expect(preview.nextExecutions).toHaveLength(2);
        expect(preview.description).toBeTruthy();
      });
    });
  });
});
