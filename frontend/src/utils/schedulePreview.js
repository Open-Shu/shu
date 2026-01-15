import CronExpressionParser from 'cron-parser';
import cronstrue from 'cronstrue';
import { formatInTimeZone } from 'date-fns-tz';

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
 * Check if a date falls within a DST transition period
 * @param {Date} date - Date to check
 * @param {string} timezone - IANA timezone identifier
 * @returns {Object} Object with isDSTTransition boolean and details
 * @private
 */
function checkDSTTransition(date, timezone) {
  try {
    // Get the timezone offset for the date
    const offset = formatInTimeZone(date, timezone, 'xxx');
    
    // Check one hour before and after
    const hourBefore = new Date(date.getTime() - 60 * 60 * 1000);
    const hourAfter = new Date(date.getTime() + 60 * 60 * 1000);
    
    const offsetBefore = formatInTimeZone(hourBefore, timezone, 'xxx');
    const offsetAfter = formatInTimeZone(hourAfter, timezone, 'xxx');
    
    // If offsets differ, we're near a DST transition
    const isDSTTransition = offsetBefore !== offset || offsetAfter !== offset;
    
    return {
      isDSTTransition,
      offset,
      offsetBefore,
      offsetAfter,
    };
  } catch (error) {
    return {
      isDSTTransition: false,
      offset: null,
    };
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
    // Request extra executions to account for potential DST skips
    const requestCount = count + 2;
    
    for (let i = 0; i < requestCount && executions.length < count; i++) {
      try {
        const next = interval.next();
        const nextDate = next.toDate();
        
        // Always include the execution, even during DST transitions
        // The cron-parser library handles DST transitions correctly
        executions.push(nextDate);
      } catch (error) {
        // If we can't get more executions, break
        break;
      }
    }

    // Return only the requested count
    return executions.slice(0, count);
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

    // Check for DST transition
    const dstCheck = checkDSTTransition(date, timezone);
    let dstNote = '';
    
    if (dstCheck.isDSTTransition) {
      // Determine if this is spring forward or fall back
      const offsetBefore = dstCheck.offsetBefore || '';
      const offsetAfter = dstCheck.offsetAfter || '';
      
      if (offsetBefore && offsetAfter && offsetBefore !== offsetAfter) {
        // Parse offset strings to compare (e.g., "-05:00" vs "-04:00")
        const beforeHours = parseInt(offsetBefore.split(':')[0], 10);
        const afterHours = parseInt(offsetAfter.split(':')[0], 10);
        
        if (afterHours > beforeHours) {
          dstNote = ' (near DST spring forward)';
        } else if (afterHours < beforeHours) {
          dstNote = ' (near DST fall back)';
        }
      }
    }

    return `${dayOfWeek}, ${monthDay} at ${time} ${tzAbbr}${dstNote}`;
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
