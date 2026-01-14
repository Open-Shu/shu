/**
 * Utility functions for formatting dates with timezone information
 */

import { format as dateFnsFormat } from 'date-fns';
import { formatInTimeZone, toZonedTime } from 'date-fns-tz';

/**
 * Format a date in a specific timezone with timezone abbreviation
 * @param {Date|string} date - Date to format
 * @param {string} timezone - IANA timezone identifier (e.g., "America/New_York")
 * @param {string} formatStr - date-fns format string (default: 'MMM d, HH:mm:ss')
 * @returns {string} Formatted date with timezone abbreviation
 */
export function formatDateInTimezone(date, timezone, formatStr = 'MMM d, HH:mm:ss') {
    if (!date) return '-';
    
    try {
        const dateObj = typeof date === 'string' ? new Date(date) : date;
        
        if (isNaN(dateObj.getTime())) {
            return '-';
        }
        
        // If no timezone specified, use local format
        if (!timezone || timezone === 'UTC') {
            return dateFnsFormat(dateObj, formatStr);
        }
        
        // Format in the specified timezone
        const formatted = formatInTimeZone(dateObj, timezone, formatStr);
        
        // Get timezone abbreviation
        const tzAbbr = getTimezoneAbbreviation(dateObj, timezone);
        
        return `${formatted} ${tzAbbr}`;
    } catch (error) {
        console.error('Error formatting date in timezone:', error);
        return dateFnsFormat(new Date(date), formatStr);
    }
}

/**
 * Get timezone abbreviation for a date in a specific timezone
 * @param {Date} date - Date object
 * @param {string} timezone - IANA timezone identifier
 * @returns {string} Timezone abbreviation (e.g., "EST", "PDT")
 */
export function getTimezoneAbbreviation(date, timezone) {
    if (!timezone || timezone === 'UTC') {
        return 'UTC';
    }
    
    try {
        const formatter = new Intl.DateTimeFormat('en-US', {
            timeZone: timezone,
            timeZoneName: 'short'
        });
        
        const parts = formatter.formatToParts(date);
        const tzPart = parts.find(part => part.type === 'timeZoneName');
        
        return tzPart ? tzPart.value : timezone;
    } catch (error) {
        console.error('Error getting timezone abbreviation:', error);
        return timezone;
    }
}

/**
 * Format a date with full details including timezone
 * @param {Date|string} date - Date to format
 * @param {string} timezone - IANA timezone identifier
 * @returns {string} Formatted date (e.g., "January 14, 2026 at 9:00 AM EST")
 */
export function formatDateTimeFull(date, timezone) {
    if (!date) return '-';
    
    try {
        const dateObj = typeof date === 'string' ? new Date(date) : date;
        
        if (isNaN(dateObj.getTime())) {
            return '-';
        }
        
        if (!timezone || timezone === 'UTC') {
            return dateFnsFormat(dateObj, 'MMMM d, yyyy \'at\' h:mm a');
        }
        
        const formatted = formatInTimeZone(dateObj, timezone, 'MMMM d, yyyy \'at\' h:mm a');
        const tzAbbr = getTimezoneAbbreviation(dateObj, timezone);
        
        return `${formatted} ${tzAbbr}`;
    } catch (error) {
        console.error('Error formatting full date time:', error);
        return dateFnsFormat(new Date(date), 'MMMM d, yyyy \'at\' h:mm a');
    }
}
