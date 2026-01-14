import CronExpressionParser from 'cron-parser';
import cronstrue from 'cronstrue';
import { format } from 'date-fns';
import { formatInTimeZone, toZonedTime } from 'date-fns-tz';

/**
 * SchedulePreview utility for generating human-readable schedule descriptions
 * and calculating next execution times for cron expressions.
 */

/**
 * Generate a human-readable description of a cron expression
 * @param {string} cron - Standard cron expression (e.g., "0 9 * * 1-5")
 * @param {string} timezone - IANA timezone identifier (e.g., "America/New_York")
 * @returns {string} Human-readable description
 */
export function describe(cron, timezone) {
  if (!cron || typeof cron !== 'string') {
    throw new Error('Invalid cron expression: must be a non-empty string');
  }

  if (!timezone || typeof timezone !== 'string') {
    throw new Error('Invalid timezone: must be a non-empty string');
  }

  try {
    // Use cronstrue to generate human-readable description
    const description = cronstrue.toString(cron, {
      throwExceptionOnParseError: true,
      verbose: false,
      use24HourTimeFormat: false,
    });

    // Get timezone abbreviation
    const now = new Date();
    const tzAbbr = formatInTimeZone(now, timezone, 'zzz');

    return `${description} (${tzAbbr})`;
  } catch (error) {
    throw new Error(`Failed to parse cron expression: ${error.message}`);
  }
}

/**
 * Calculate the next N execution times for a cron expression
 * @param {string} cron - Standard cron expression
 * @param {string} timezone - IANA timezone identifier
 * @param {number} count - Number of execution times to calculate (default: 5)
 * @returns {Date[]} Array of Date objects representing next execution times
 */
export function getNextExecutions(cron, timezone, count = 5) {
  if (!cron || typeof cron !== 'string') {
    throw new Error('Invalid cron expression: must be a non-empty string');
  }

  if (!timezone || typeof timezone !== 'string') {
    throw new Error('Invalid timezone: must be a non-empty string');
  }

  if (!Number.isInteger(count) || count < 1 || count > 10) {
    throw new Error('Count must be an integer between 1 and 10');
  }

  try {
    // Parse the cron expression with timezone
    const options = {
      currentDate: new Date(),
      tz: timezone,
    };

    const interval = CronExpressionParser.parse(cron, options);
    const executions = [];

    // Get the next N execution times
    for (let i = 0; i < count; i++) {
      try {
        const next = interval.next();
        executions.push(next.toDate());
      } catch (error) {
        // If we can't get more executions, break
        break;
      }
    }

    return executions;
  } catch (error) {
    throw new Error(`Failed to calculate next executions: ${error.message}`);
  }
}

/**
 * Format an execution time with timezone information
 * @param {Date} date - Date object to format
 * @param {string} timezone - IANA timezone identifier
 * @returns {string} Formatted string (e.g., "Tuesday, January 14, 2026 at 9:00 AM EST")
 */
export function formatExecution(date, timezone) {
  if (!(date instanceof Date) || isNaN(date.getTime())) {
    throw new Error('Invalid date: must be a valid Date object');
  }

  if (!timezone || typeof timezone !== 'string') {
    throw new Error('Invalid timezone: must be a non-empty string');
  }

  try {
    // Format the date in the specified timezone
    const dayOfWeek = formatInTimeZone(date, timezone, 'EEEE');
    const monthDay = formatInTimeZone(date, timezone, 'MMMM d, yyyy');
    const time = formatInTimeZone(date, timezone, 'h:mm a');
    const tzAbbr = formatInTimeZone(date, timezone, 'zzz');

    return `${dayOfWeek}, ${monthDay} at ${time} ${tzAbbr}`;
  } catch (error) {
    throw new Error(`Failed to format execution time: ${error.message}`);
  }
}

/**
 * Get a preview of the schedule including description and next execution times
 * @param {string} cron - Standard cron expression
 * @param {string} timezone - IANA timezone identifier
 * @param {number} count - Number of execution times to show (default: 5)
 * @returns {Object} Object with description and formatted execution times
 */
export function getSchedulePreview(cron, timezone, count = 5) {
  if (!cron || typeof cron !== 'string') {
    throw new Error('Invalid cron expression: must be a non-empty string');
  }

  if (!timezone || typeof timezone !== 'string') {
    throw new Error('Invalid timezone: must be a non-empty string');
  }

  try {
    const description = describe(cron, timezone);
    const executions = getNextExecutions(cron, timezone, count);
    const formattedExecutions = executions.map(date => formatExecution(date, timezone));

    return {
      description,
      nextExecutions: formattedExecutions,
      executionDates: executions,
    };
  } catch (error) {
    throw new Error(`Failed to generate schedule preview: ${error.message}`);
  }
}

// Export all functions as named exports
export default {
  describe,
  getNextExecutions,
  formatExecution,
  getSchedulePreview,
};
