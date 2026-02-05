import CronExpressionParser from 'cron-parser';

/**
 * Validation utilities for schedule configuration (cron expressions and timezones)
 *
 * This module provides comprehensive validation for:
 * - Cron expressions (syntax, semantics, and practical constraints)
 * - Timezone identifiers (IANA format)
 * - Combined schedule configurations
 *
 * All validation functions return objects with:
 * - isValid: boolean indicating if validation passed
 * - error: string with clear, actionable error message (only if isValid is false)
 */

/**
 * Validate a cron expression for syntax and semantic correctness
 *
 * @param {string} cronExpression - The cron expression to validate
 * @returns {Object} Validation result with isValid boolean and error message
 *
 * @example
 * validateCronExpression('0 9 * * *')
 * // Returns: { isValid: true }
 *
 * validateCronExpression('invalid')
 * // Returns: { isValid: false, error: 'Cron expression must have 5 or 6 parts...' }
 */
export function validateCronExpression(cronExpression) {
  // Check for null, undefined, or empty string
  if (!cronExpression || typeof cronExpression !== 'string') {
    return {
      isValid: false,
      error: 'Cron expression is required and must be a non-empty string',
    };
  }

  const trimmed = cronExpression.trim();

  // Check if empty after trimming
  if (trimmed.length === 0) {
    return {
      isValid: false,
      error: 'Cron expression cannot be empty or contain only whitespace',
    };
  }

  // Split into parts
  const parts = trimmed.split(/\s+/);

  // Standard cron has 5 parts: minute hour day month weekday
  // Some systems support 6 parts with seconds: second minute hour day month weekday
  if (parts.length !== 5 && parts.length !== 6) {
    return {
      isValid: false,
      error: `Cron expression must have 5 or 6 parts (got ${parts.length}). Example: "0 9 * * *" for daily at 9 AM`,
    };
  }

  // Define validation rules for each field
  const fieldValidations = [
    { name: 'second', range: [0, 59], optional: true },
    { name: 'minute', range: [0, 59] },
    { name: 'hour', range: [0, 23] },
    { name: 'day of month', range: [1, 31] },
    { name: 'month', range: [1, 12] },
    { name: 'day of week', range: [0, 7] }, // 0 and 7 both represent Sunday
  ];

  const startIndex = parts.length === 6 ? 0 : 1; // Skip seconds if not present

  // Validate each part
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    const validation = fieldValidations[startIndex + i];

    if (!validation) {
      continue;
    }

    // Validate the field
    const fieldResult = validateCronField(part, validation.name, validation.range);
    if (!fieldResult.isValid) {
      return fieldResult;
    }
  }

  // Use cron-parser library for comprehensive validation
  try {
    CronExpressionParser.parse(trimmed);
  } catch (error) {
    // Extract meaningful error message
    let errorMessage = error.message || 'Invalid cron expression';

    // Make error messages more user-friendly
    if (errorMessage.includes('Invalid characters')) {
      errorMessage = 'Cron expression contains invalid characters. Use only numbers, *, -, /, and ,';
    } else if (errorMessage.includes('out of range')) {
      errorMessage = 'One or more values in the cron expression are out of valid range';
    } else if (errorMessage.includes('Invalid cron expression')) {
      errorMessage = 'The cron expression format is invalid. Example: "0 9 * * *" for daily at 9 AM';
    }

    return {
      isValid: false,
      error: errorMessage,
    };
  }

  return { isValid: true };
}

/**
 * Validate a single cron field (minute, hour, day, etc.)
 *
 * @param {string} field - The field value to validate
 * @param {string} fieldName - Name of the field for error messages
 * @param {Array<number>} range - Valid range [min, max] for the field
 * @returns {Object} Validation result
 *
 * @private
 */
function validateCronField(field, fieldName, range) {
  // Allow wildcards
  if (field === '*' || field === '?') {
    return { isValid: true };
  }

  // Handle ranges (e.g., "1-5")
  if (field.includes('-')) {
    const rangeParts = field.split('-');
    if (rangeParts.length !== 2) {
      return {
        isValid: false,
        error: `Invalid range in ${fieldName}: "${field}". Use format "start-end" (e.g., "1-5")`,
      };
    }

    const [start, end] = rangeParts.map((p) => parseInt(p, 10));
    if (isNaN(start) || isNaN(end)) {
      return {
        isValid: false,
        error: `Invalid range in ${fieldName}: "${field}". Both start and end must be numbers`,
      };
    }

    if (start < range[0] || start > range[1] || end < range[0] || end > range[1]) {
      return {
        isValid: false,
        error: `Range in ${fieldName} is out of bounds: "${field}". Valid range is ${range[0]}-${range[1]}`,
      };
    }

    if (start > end) {
      return {
        isValid: false,
        error: `Invalid range in ${fieldName}: "${field}". Start value must be less than or equal to end value`,
      };
    }

    return { isValid: true };
  }

  // Handle step values (e.g., "*/5" or "1-10/2")
  if (field.includes('/')) {
    const stepParts = field.split('/');
    if (stepParts.length !== 2) {
      return {
        isValid: false,
        error: `Invalid step value in ${fieldName}: "${field}". Use format "range/step" (e.g., "*/5" or "1-10/2")`,
      };
    }

    const step = parseInt(stepParts[1], 10);
    if (isNaN(step) || step < 1) {
      return {
        isValid: false,
        error: `Invalid step value in ${fieldName}: "${field}". Step must be a positive number`,
      };
    }

    // Validate the base part (before the /)
    if (stepParts[0] !== '*') {
      return validateCronField(stepParts[0], fieldName, range);
    }

    return { isValid: true };
  }

  // Handle lists (e.g., "1,3,5")
  if (field.includes(',')) {
    const listParts = field.split(',');
    for (const part of listParts) {
      const result = validateCronField(part.trim(), fieldName, range);
      if (!result.isValid) {
        return result;
      }
    }
    return { isValid: true };
  }

  // Handle single numeric value
  const num = parseInt(field, 10);
  if (isNaN(num)) {
    return {
      isValid: false,
      error: `Invalid value in ${fieldName}: "${field}". Must be a number, wildcard (*), range (1-5), step (*/5), or list (1,3,5)`,
    };
  }

  if (num < range[0] || num > range[1]) {
    return {
      isValid: false,
      error: `Value in ${fieldName} is out of range: ${num}. Valid range is ${range[0]}-${range[1]}`,
    };
  }

  return { isValid: true };
}

/**
 * Validate a timezone identifier (IANA format)
 *
 * @param {string} timezone - The timezone identifier to validate (e.g., "America/New_York")
 * @returns {Object} Validation result with isValid boolean and error message
 *
 * @example
 * validateTimezone('America/New_York')
 * // Returns: { isValid: true }
 *
 * validateTimezone('Invalid/Timezone')
 * // Returns: { isValid: false, error: 'Invalid timezone identifier...' }
 */
export function validateTimezone(timezone) {
  // Check for null, undefined, or empty string
  if (!timezone || typeof timezone !== 'string') {
    return {
      isValid: false,
      error: 'Timezone is required and must be a non-empty string',
    };
  }

  const trimmed = timezone.trim();

  // Check if empty after trimming
  if (trimmed.length === 0) {
    return {
      isValid: false,
      error: 'Timezone cannot be empty or contain only whitespace',
    };
  }

  // Try to use the timezone with Intl.DateTimeFormat to validate it
  try {
    // This will throw an error if the timezone is invalid
    Intl.DateTimeFormat('en-US', { timeZone: trimmed });
    return { isValid: true };
  } catch (error) {
    return {
      isValid: false,
      error: `Invalid timezone identifier: "${trimmed}". Please select a valid timezone from the list (e.g., "America/New_York", "Europe/London", "UTC")`,
    };
  }
}

/**
 * Validate day-of-month selections for different months and leap years
 *
 * @param {string} cronExpression - The cron expression to validate
 * @returns {Object} Validation result with isValid boolean and warnings array
 */
export function validateDayOfMonthEdgeCases(cronExpression) {
  const warnings = [];

  if (!cronExpression || typeof cronExpression !== 'string') {
    return { isValid: true, warnings };
  }

  const parts = cronExpression.trim().split(/\s+/);

  // Only validate 5-part cron expressions
  if (parts.length !== 5) {
    return { isValid: true, warnings };
  }

  // eslint-disable-next-line no-unused-vars
  const [_minute, _hour, dayOfMonth, month, dayOfWeek] = parts;

  // Skip validation if day-of-month is wildcard or if day-of-week is specified
  if (dayOfMonth === '*' || dayOfWeek !== '*') {
    return { isValid: true, warnings };
  }

  // Parse day-of-month values
  const dayValues = [];

  // Handle ranges (e.g., "1-5")
  if (dayOfMonth.includes('-') && !dayOfMonth.includes(',')) {
    const [start, end] = dayOfMonth.split('-').map((d) => parseInt(d, 10));
    for (let i = start; i <= end; i++) {
      dayValues.push(i);
    }
  }
  // Handle lists (e.g., "1,15,30")
  else if (dayOfMonth.includes(',')) {
    dayValues.push(...dayOfMonth.split(',').map((d) => parseInt(d.trim(), 10)));
  }
  // Handle single value
  else if (!dayOfMonth.includes('/') && !dayOfMonth.includes('*')) {
    dayValues.push(parseInt(dayOfMonth, 10));
  }

  // Check for days that don't exist in all months
  const problematicDays = dayValues.filter((day) => day > 28);

  if (problematicDays.length > 0) {
    // Check if month is specified
    const isMonthSpecific = month !== '*';

    if (!isMonthSpecific) {
      // Days 29-31 don't exist in all months
      if (problematicDays.some((day) => day === 29)) {
        warnings.push(
          'Day 29 is scheduled but does not exist in February (except leap years). ' +
            'The schedule will skip February in non-leap years.'
        );
      }
      if (problematicDays.some((day) => day === 30)) {
        warnings.push('Day 30 is scheduled but does not exist in February. ' + 'The schedule will skip February.');
      }
      if (problematicDays.some((day) => day === 31)) {
        warnings.push(
          'Day 31 is scheduled but only exists in 7 months (Jan, Mar, May, Jul, Aug, Oct, Dec). ' +
            'The schedule will skip months with fewer days.'
        );
      }
    } else {
      // Validate specific month constraints
      const monthValues = [];

      if (month.includes(',')) {
        monthValues.push(...month.split(',').map((m) => parseInt(m.trim(), 10)));
      } else if (month.includes('-')) {
        const [start, end] = month.split('-').map((m) => parseInt(m, 10));
        for (let i = start; i <= end; i++) {
          monthValues.push(i);
        }
      } else if (!month.includes('/') && !month.includes('*')) {
        monthValues.push(parseInt(month, 10));
      }

      // Check each month
      const monthsWith30Days = [4, 6, 9, 11]; // April, June, September, November
      // const monthsWith31Days = [1, 3, 5, 7, 8, 10, 12]; // Jan, Mar, May, Jul, Aug, Oct, Dec

      monthValues.forEach((monthNum) => {
        if (monthNum === 2) {
          // February
          if (problematicDays.some((day) => day > 29)) {
            warnings.push(
              `Days ${problematicDays.filter((d) => d > 29).join(', ')} are scheduled for February but do not exist. ` +
                'The schedule will not run on these days in February.'
            );
          } else if (problematicDays.some((day) => day === 29)) {
            warnings.push(
              'Day 29 is scheduled for February but only exists in leap years. ' +
                'The schedule will skip non-leap years.'
            );
          }
        } else if (monthsWith30Days.includes(monthNum)) {
          // Months with 30 days
          if (problematicDays.some((day) => day === 31)) {
            const monthName = new Date(2000, monthNum - 1, 1).toLocaleString('en-US', { month: 'long' });
            warnings.push(
              `Day 31 is scheduled for ${monthName} but this month only has 30 days. ` +
                'The schedule will not run on day 31.'
            );
          }
        }
      });
    }
  }

  return { isValid: true, warnings };
}

/**
 * Validate a complete schedule configuration (cron + timezone)
 *
 * @param {Object} config - Schedule configuration object
 * @param {string} config.cron - Cron expression
 * @param {string} config.timezone - Timezone identifier
 * @returns {Object} Validation result with isValid boolean, errors object, and warnings array
 *
 * @example
 * validateScheduleConfig({ cron: '0 9 * * *', timezone: 'America/New_York' })
 * // Returns: { isValid: true, errors: {}, warnings: [] }
 *
 * validateScheduleConfig({ cron: 'invalid', timezone: 'Invalid/TZ' })
 * // Returns: {
 * //   isValid: false,
 * //   errors: {
 * //     cron: 'Cron expression must have 5 or 6 parts...',
 * //     timezone: 'Invalid timezone identifier...'
 * //   },
 * //   warnings: []
 * // }
 *
 * validateScheduleConfig({ cron: '0 9 31 * *', timezone: 'UTC' })
 * // Returns: {
 * //   isValid: true,
 * //   errors: {},
 * //   warnings: ['Day 31 is scheduled but only exists in 7 months...']
 * // }
 */
export function validateScheduleConfig(config) {
  if (!config || typeof config !== 'object') {
    return {
      isValid: false,
      errors: {
        general: 'Schedule configuration must be an object with cron and timezone properties',
      },
      warnings: [],
    };
  }

  const errors = {};
  const warnings = [];

  // Validate cron expression
  const cronResult = validateCronExpression(config.cron);
  if (!cronResult.isValid) {
    errors.cron = cronResult.error;
  }

  // Validate timezone
  const timezoneResult = validateTimezone(config.timezone);
  if (!timezoneResult.isValid) {
    errors.timezone = timezoneResult.error;
  }

  // Validate day-of-month edge cases (only if cron is valid)
  if (cronResult.isValid && config.cron) {
    const dayValidation = validateDayOfMonthEdgeCases(config.cron);
    warnings.push(...dayValidation.warnings);
  }

  return {
    isValid: Object.keys(errors).length === 0,
    errors,
    warnings,
  };
}

/**
 * Check if a cron expression is valid without returning detailed errors
 *
 * @param {string} cronExpression - The cron expression to check
 * @returns {boolean} True if valid, false otherwise
 *
 * @example
 * isValidCronExpression('0 9 * * *')
 * // Returns: true
 *
 * isValidCronExpression('invalid')
 * // Returns: false
 */
export function isValidCronExpression(cronExpression) {
  return validateCronExpression(cronExpression).isValid;
}

/**
 * Check if a timezone is valid without returning detailed errors
 *
 * @param {string} timezone - The timezone identifier to check
 * @returns {boolean} True if valid, false otherwise
 *
 * @example
 * isValidTimezone('America/New_York')
 * // Returns: true
 *
 * isValidTimezone('Invalid/Timezone')
 * // Returns: false
 */
export function isValidTimezone(timezone) {
  return validateTimezone(timezone).isValid;
}

// Export all functions
export default {
  validateCronExpression,
  validateTimezone,
  validateScheduleConfig,
  isValidCronExpression,
  isValidTimezone,
};
