/**
 * Unit tests for timezone formatter utilities
 */

import {
    formatDateInTimezone,
    getTimezoneAbbreviation,
    formatDateTimeFull
} from '../timezoneFormatter';

describe('timezoneFormatter', () => {
    describe('formatDateInTimezone', () => {
        it('formats date in specified timezone with abbreviation', () => {
            const date = new Date('2026-01-14T14:00:00Z');
            const result = formatDateInTimezone(date, 'America/New_York');
            
            // Should include time and timezone abbreviation
            expect(result).toContain('EST');
            expect(result).toMatch(/Jan 14/);
        });

        it('handles UTC timezone', () => {
            const date = new Date('2026-01-14T14:00:00Z');
            const result = formatDateInTimezone(date, 'UTC');
            
            expect(result).toMatch(/Jan 14/);
        });

        it('handles null date', () => {
            const result = formatDateInTimezone(null, 'America/New_York');
            expect(result).toBe('-');
        });

        it('handles invalid date', () => {
            const result = formatDateInTimezone('invalid', 'America/New_York');
            expect(result).toBe('-');
        });

        it('handles string date input', () => {
            const result = formatDateInTimezone('2026-01-14T14:00:00Z', 'America/New_York');
            expect(result).toContain('EST');
        });
    });

    describe('getTimezoneAbbreviation', () => {
        it('returns timezone abbreviation for valid timezone', () => {
            const date = new Date('2026-01-14T14:00:00Z');
            const result = getTimezoneAbbreviation(date, 'America/New_York');
            
            expect(result).toBe('EST');
        });

        it('returns UTC for UTC timezone', () => {
            const date = new Date('2026-01-14T14:00:00Z');
            const result = getTimezoneAbbreviation(date, 'UTC');
            
            expect(result).toBe('UTC');
        });

        it('handles daylight saving time', () => {
            const summerDate = new Date('2026-07-14T14:00:00Z');
            const result = getTimezoneAbbreviation(summerDate, 'America/New_York');
            
            expect(result).toBe('EDT');
        });
    });

    describe('formatDateTimeFull', () => {
        it('formats date with full details and timezone', () => {
            const date = new Date('2026-01-14T14:00:00Z');
            const result = formatDateTimeFull(date, 'America/New_York');
            
            expect(result).toContain('January 14, 2026');
            expect(result).toContain('at');
            expect(result).toContain('EST');
        });

        it('handles null date', () => {
            const result = formatDateTimeFull(null, 'America/New_York');
            expect(result).toBe('-');
        });

        it('handles UTC timezone', () => {
            const date = new Date('2026-01-14T14:00:00Z');
            const result = formatDateTimeFull(date, 'UTC');
            
            expect(result).toContain('January 14, 2026');
            expect(result).toContain('at');
        });
    });

});
