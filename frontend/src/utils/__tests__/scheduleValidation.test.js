import {
  validateCronExpression,
  validateTimezone,
  validateScheduleConfig,
  isValidCronExpression,
  isValidTimezone,
} from '../scheduleValidation';

describe('scheduleValidation', () => {
  describe('validateCronExpression', () => {
    test('validates correct 5-part cron expression', () => {
      const result = validateCronExpression('0 9 * * *');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('validates correct 6-part cron expression with seconds', () => {
      const result = validateCronExpression('0 0 9 * * *');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('validates cron expression with ranges', () => {
      const result = validateCronExpression('0 9 * * 1-5');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('validates cron expression with step values', () => {
      const result = validateCronExpression('*/15 * * * *');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('validates cron expression with lists', () => {
      const result = validateCronExpression('0 9,12,15 * * *');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('validates cron expression with wildcards', () => {
      const result = validateCronExpression('* * * * *');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('rejects null cron expression', () => {
      const result = validateCronExpression(null);
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('required');
    });

    test('rejects undefined cron expression', () => {
      const result = validateCronExpression(undefined);
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('required');
    });

    test('rejects empty string cron expression', () => {
      const result = validateCronExpression('');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('required');
    });

    test('rejects whitespace-only cron expression', () => {
      const result = validateCronExpression('   ');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('empty');
    });

    test('rejects cron expression with too few parts', () => {
      const result = validateCronExpression('0 9 * *');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('5 or 6 parts');
    });

    test('rejects cron expression with too many parts', () => {
      const result = validateCronExpression('0 0 9 * * * *');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('5 or 6 parts');
    });

    test('rejects cron expression with invalid minute value', () => {
      const result = validateCronExpression('60 9 * * *');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('minute');
    });

    test('rejects cron expression with invalid hour value', () => {
      const result = validateCronExpression('0 24 * * *');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('hour');
    });

    test('rejects cron expression with invalid day of month', () => {
      const result = validateCronExpression('0 9 32 * *');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('day');
    });

    test('rejects cron expression with invalid month', () => {
      const result = validateCronExpression('0 9 * 13 *');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('month');
    });

    test('rejects cron expression with invalid day of week', () => {
      const result = validateCronExpression('0 9 * * 8');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('day of week');
    });

    test('rejects cron expression with invalid range', () => {
      const result = validateCronExpression('0 9 * * 5-2');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('range');
    });

    test('rejects cron expression with invalid step value', () => {
      const result = validateCronExpression('*/0 * * * *');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('step');
    });

    test('provides clear error message for invalid format', () => {
      const result = validateCronExpression('invalid');
      expect(result.isValid).toBe(false);
      expect(result.error).toBeTruthy();
      expect(result.error.length).toBeGreaterThan(0);
    });
  });

  describe('validateTimezone', () => {
    test('validates correct IANA timezone', () => {
      const result = validateTimezone('America/New_York');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('validates UTC timezone', () => {
      const result = validateTimezone('UTC');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('validates European timezone', () => {
      const result = validateTimezone('Europe/London');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('validates Asian timezone', () => {
      const result = validateTimezone('Asia/Tokyo');
      expect(result.isValid).toBe(true);
      expect(result.error).toBeUndefined();
    });

    test('rejects null timezone', () => {
      const result = validateTimezone(null);
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('required');
    });

    test('rejects undefined timezone', () => {
      const result = validateTimezone(undefined);
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('required');
    });

    test('rejects empty string timezone', () => {
      const result = validateTimezone('');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('required');
    });

    test('rejects whitespace-only timezone', () => {
      const result = validateTimezone('   ');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('empty');
    });

    test('rejects invalid timezone identifier', () => {
      const result = validateTimezone('Invalid/Timezone');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('Invalid timezone');
    });

    test('rejects malformed timezone identifier', () => {
      const result = validateTimezone('Not A Timezone');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('Invalid timezone');
    });

    test('provides clear error message with example', () => {
      const result = validateTimezone('BadTimezone');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('America/New_York');
    });
  });

  describe('validateScheduleConfig', () => {
    test('validates correct schedule configuration', () => {
      const result = validateScheduleConfig({
        cron: '0 9 * * *',
        timezone: 'America/New_York',
      });
      expect(result.isValid).toBe(true);
      expect(result.errors).toEqual({});
    });

    test('rejects configuration with invalid cron', () => {
      const result = validateScheduleConfig({
        cron: 'invalid',
        timezone: 'America/New_York',
      });
      expect(result.isValid).toBe(false);
      expect(result.errors.cron).toBeTruthy();
      expect(result.errors.timezone).toBeUndefined();
    });

    test('rejects configuration with invalid timezone', () => {
      const result = validateScheduleConfig({
        cron: '0 9 * * *',
        timezone: 'Invalid/Timezone',
      });
      expect(result.isValid).toBe(false);
      expect(result.errors.timezone).toBeTruthy();
      expect(result.errors.cron).toBeUndefined();
    });

    test('rejects configuration with both invalid', () => {
      const result = validateScheduleConfig({
        cron: 'invalid',
        timezone: 'Invalid/Timezone',
      });
      expect(result.isValid).toBe(false);
      expect(result.errors.cron).toBeTruthy();
      expect(result.errors.timezone).toBeTruthy();
    });

    test('rejects null configuration', () => {
      const result = validateScheduleConfig(null);
      expect(result.isValid).toBe(false);
      expect(result.errors.general).toBeTruthy();
    });

    test('rejects undefined configuration', () => {
      const result = validateScheduleConfig(undefined);
      expect(result.isValid).toBe(false);
      expect(result.errors.general).toBeTruthy();
    });

    test('rejects non-object configuration', () => {
      const result = validateScheduleConfig('not an object');
      expect(result.isValid).toBe(false);
      expect(result.errors.general).toBeTruthy();
    });
  });

  describe('isValidCronExpression', () => {
    test('returns true for valid expression', () => {
      expect(isValidCronExpression('0 9 * * *')).toBe(true);
    });

    test('returns false for invalid expression', () => {
      expect(isValidCronExpression('invalid')).toBe(false);
    });

    test('returns false for null', () => {
      expect(isValidCronExpression(null)).toBe(false);
    });

    test('returns false for undefined', () => {
      expect(isValidCronExpression(undefined)).toBe(false);
    });

    test('returns false for empty string', () => {
      expect(isValidCronExpression('')).toBe(false);
    });
  });

  describe('isValidTimezone', () => {
    test('returns true for valid timezone', () => {
      expect(isValidTimezone('America/New_York')).toBe(true);
    });

    test('returns false for invalid timezone', () => {
      expect(isValidTimezone('Invalid/Timezone')).toBe(false);
    });

    test('returns false for null', () => {
      expect(isValidTimezone(null)).toBe(false);
    });

    test('returns false for undefined', () => {
      expect(isValidTimezone(undefined)).toBe(false);
    });

    test('returns false for empty string', () => {
      expect(isValidTimezone('')).toBe(false);
    });
  });

  describe('edge cases', () => {
    test('handles cron expression with extra whitespace', () => {
      const result = validateCronExpression('  0   9   *   *   *  ');
      expect(result.isValid).toBe(true);
    });

    test('handles timezone with extra whitespace', () => {
      const result = validateTimezone('  America/New_York  ');
      expect(result.isValid).toBe(true);
    });

    test('validates complex cron expression with multiple features', () => {
      const result = validateCronExpression('*/15 9-17 * * 1-5');
      expect(result.isValid).toBe(true);
    });

    test('validates cron expression with question mark', () => {
      const result = validateCronExpression('0 9 ? * *');
      expect(result.isValid).toBe(true);
    });

    test('provides actionable error messages', () => {
      const result = validateCronExpression('0 25 * * *');
      expect(result.isValid).toBe(false);
      expect(result.error).toContain('hour');
      expect(result.error).toContain('0-23');
    });
  });
});
