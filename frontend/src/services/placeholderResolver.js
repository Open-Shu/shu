/**
 * PlaceholderResolver service for handling system prefilling and placeholder resolution
 * in the experience import wizard.
 * 
 * TODO: Add comprehensive trigger_config validation
 * - Validate cron expressions for cron triggers
 * - Validate scheduled time formats for scheduled triggers
 * - Context-aware validation based on trigger_type
 */
class PlaceholderResolver {

  /**
   * Validate user-provided input values for placeholders
   * @param {Array<string>} placeholders - Array of placeholder names that need user input
   * @param {Object} values - Object mapping placeholder names to user-provided values
   * @returns {Object} Validation result with isValid boolean and errors array
   */
  validateUserInputs(placeholders = [], values = {}) {
    const result = {
      isValid: true,
      errors: []
    };

    // Check that all required placeholders have values
    const missingPlaceholders = placeholders.filter(placeholder => {
      const value = values[placeholder];
      return value === undefined || value === null || 
             (typeof value === 'string' && value.trim() === '');
    });

    if (missingPlaceholders.length > 0) {
      result.errors.push(`Missing values for required placeholders: ${missingPlaceholders.join(', ')}`);
    }

    // Validate specific placeholder types
    placeholders.forEach(placeholder => {
      const value = values[placeholder];
      
      if (value !== undefined && value !== null) {
        // Validate timezone format if it's a timezone placeholder
        if (placeholder.includes('timezone') && typeof value === 'string') {
          if (!this._isValidTimezone(value)) {
            result.errors.push(`Invalid timezone format for ${placeholder}: ${value}`);
          }
        }

        // Validate that provider/model placeholders are not empty strings
        if ((placeholder.includes('provider') || placeholder.includes('model')) && 
            typeof value === 'string' && value.trim() === '') {
          result.errors.push(`${placeholder} cannot be empty`);
        }

        // Validate trigger_type values
        if (placeholder.includes('trigger_type') && typeof value === 'string') {
          const validTriggerTypes = ['manual', 'cron', 'scheduled'];
          if (!validTriggerTypes.includes(value.toLowerCase())) {
            result.errors.push(`Invalid trigger_type for ${placeholder}: ${value}. Must be one of: ${validTriggerTypes.join(', ')}`);
          }
        }

        // Validate max_run_seconds is a positive number
        if (placeholder.includes('max_run_seconds')) {
          const numValue = typeof value === 'string' ? parseInt(value, 10) : value;
          if (isNaN(numValue) || numValue <= 0) {
            result.errors.push(`${placeholder} must be a positive number`);
          }
        }

        // TODO: Add trigger_config validation
        // This should validate cron expressions, scheduled times, etc. based on trigger_type
        // For now, just ensure it's not empty
        if (placeholder.includes('trigger_config') && typeof value === 'string') {
          if (value.trim() === '') {
            result.errors.push(`${placeholder} cannot be empty`);
          }
        }
      }
    });

    result.isValid = result.errors.length === 0;
    return result;
  }

  /**
   * Get placeholder metadata for form generation
   * @param {string} placeholderName - Name of the placeholder
   * @returns {Object} Metadata object with type, description, and options
   */
  getPlaceholderMetadata(placeholderName) {
    const metadata = {
      name: placeholderName,
      type: 'text', // Default type
      description: `Value for ${placeholderName}`,
      required: true,
      options: null
    };

    // Special handling for known placeholder patterns
    if (placeholderName === 'selected_provider') {
      metadata.type = 'dropdown';
      metadata.description = 'Select the LLM provider for this experience';
      // Options will be populated by the component from API
    } else if (placeholderName === 'selected_model') {
      metadata.type = 'dropdown';
      metadata.description = 'Select the model to use with the chosen provider';
      // Options will be populated by the component from API
    } else if (placeholderName === 'trigger_type') {
      metadata.type = 'dropdown';
      metadata.description = 'How the experience will be triggered';
      metadata.options = [
        { label: 'Manual', value: 'manual' },
        { label: 'Cron Schedule', value: 'cron' },
        { label: 'Scheduled', value: 'scheduled' }
      ];
    } else if (placeholderName === 'trigger_config') {
      metadata.type = 'text';
      metadata.description = 'Trigger configuration (e.g., cron expression like "0 7 * * *" for daily at 7 AM)';
    } else if (placeholderName === 'max_run_seconds') {
      metadata.type = 'number';
      metadata.description = 'Maximum runtime in seconds (e.g., 300 for 5 minutes)';
    } else if (placeholderName.includes('timezone')) {
      metadata.type = 'timezone';
      metadata.description = 'Select your timezone for scheduling';
    } else if (placeholderName.includes('email')) {
      metadata.type = 'email';
      metadata.description = 'Enter email address';
    } else if (placeholderName.includes('name')) {
      metadata.type = 'text';
      metadata.description = 'Enter name';
    } else if (placeholderName.includes('cron')) {
      metadata.type = 'text';
      metadata.description = 'Enter cron expression (e.g., "0 7 * * *" for 7 AM daily)';
    }

    return metadata;
  }

  /**
   * Check if a timezone string is valid
   * @private
   * @param {string} timezone - Timezone string to validate
   * @returns {boolean} True if valid timezone
   */
  _isValidTimezone(timezone) {
    try {
      // Try to create a date with the timezone to validate it
      Intl.DateTimeFormat(undefined, { timeZone: timezone });
      return true;
    } catch (error) {
      return false;
    }
  }

  /**
   * Get list of common timezone options for dropdowns
   * @returns {Array<Object>} Array of timezone options with label and value
   */
  getTimezoneOptions() {
    const commonTimezones = [
      'UTC',
      'America/New_York',
      'America/Chicago', 
      'America/Denver',
      'America/Los_Angeles',
      'America/Toronto',
      'America/Vancouver',
      'Europe/London',
      'Europe/Paris',
      'Europe/Berlin',
      'Europe/Rome',
      'Asia/Tokyo',
      'Asia/Shanghai',
      'Asia/Kolkata',
      'Australia/Sydney',
      'Australia/Melbourne'
    ];

    return commonTimezones.map(tz => ({
      label: tz.replace('_', ' '),
      value: tz
    }));
  }
}

// Export a singleton instance
export default new PlaceholderResolver();
