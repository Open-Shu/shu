import PlaceholderResolver from '../placeholderResolver';

describe('PlaceholderResolver', () => {

  describe('validateUserInputs', () => {
    it('validates that all required placeholders have values', () => {
      const placeholders = ['selected_provider', 'selected_model', 'experience_name'];
      const values = {
        selected_provider: 'openai',
        selected_model: 'gpt-4',
        experience_name: 'Morning Briefing'
      };

      const result = PlaceholderResolver.validateUserInputs(placeholders, values);
      
      expect(result.isValid).toBe(true);
      expect(result.errors).toEqual([]);
    });

    it('reports missing required placeholders', () => {
      const placeholders = ['selected_provider', 'selected_model', 'experience_name'];
      const values = {
        selected_provider: 'openai'
        // missing selected_model and experience_name
      };

      const result = PlaceholderResolver.validateUserInputs(placeholders, values);
      
      expect(result.isValid).toBe(false);
      expect(result.errors).toContain('Missing values for required placeholders: selected_model, experience_name');
    });

    it('treats empty strings as missing values', () => {
      const placeholders = ['selected_provider'];
      const values = {
        selected_provider: '   ' // whitespace only
      };

      const result = PlaceholderResolver.validateUserInputs(placeholders, values);
      
      expect(result.isValid).toBe(false);
      expect(result.errors).toContain('Missing values for required placeholders: selected_provider');
    });

    it('validates timezone format', () => {
      const placeholders = ['user_timezone'];
      const values = {
        user_timezone: 'Invalid/Timezone'
      };

      const result = PlaceholderResolver.validateUserInputs(placeholders, values);
      
      expect(result.isValid).toBe(false);
      expect(result.errors).toContain('Invalid timezone format for user_timezone: Invalid/Timezone');
    });

    it('validates provider and model placeholders are not empty', () => {
      const placeholders = ['selected_provider', 'selected_model'];
      const values = {
        selected_provider: '',
        selected_model: '   '
      };

      const result = PlaceholderResolver.validateUserInputs(placeholders, values);
      
      expect(result.isValid).toBe(false);
      expect(result.errors).toContain('selected_provider cannot be empty');
      expect(result.errors).toContain('selected_model cannot be empty');
    });

    it('validates trigger_type values', () => {
      const placeholders = ['trigger_type'];
      const values = {
        trigger_type: 'invalid_type'
      };

      const result = PlaceholderResolver.validateUserInputs(placeholders, values);
      
      expect(result.isValid).toBe(false);
      expect(result.errors).toContain('Invalid trigger_type for trigger_type: invalid_type. Must be one of: manual, cron, scheduled');
    });

    it('accepts valid trigger_type values', () => {
      const placeholders = ['trigger_type'];
      const validTypes = ['manual', 'cron', 'scheduled'];

      validTypes.forEach(type => {
        const values = { trigger_type: type };
        const result = PlaceholderResolver.validateUserInputs(placeholders, values);
        expect(result.isValid).toBe(true);
      });
    });

    it('validates max_run_seconds is a positive number', () => {
      const placeholders = ['max_run_seconds'];
      
      // Test invalid values
      const invalidValues = ['0', '-1', 'abc', ''];
      invalidValues.forEach(value => {
        const values = { max_run_seconds: value };
        const result = PlaceholderResolver.validateUserInputs(placeholders, values);
        expect(result.isValid).toBe(false);
        expect(result.errors).toContain('max_run_seconds must be a positive number');
      });

      // Test valid values
      const validValues = ['300', '60', 1800];
      validValues.forEach(value => {
        const values = { max_run_seconds: value };
        const result = PlaceholderResolver.validateUserInputs(placeholders, values);
        expect(result.isValid).toBe(true);
      });
    });

    it('validates trigger_config is not empty', () => {
      const placeholders = ['trigger_config'];
      const values = {
        trigger_config: '   '
      };

      const result = PlaceholderResolver.validateUserInputs(placeholders, values);
      
      expect(result.isValid).toBe(false);
      expect(result.errors).toContain('trigger_config cannot be empty');

      // Test valid trigger_config
      const validValues = {
        trigger_config: '0 7 * * *'
      };
      const validResult = PlaceholderResolver.validateUserInputs(placeholders, validValues);
      expect(validResult.isValid).toBe(true);
    });

    it('accepts valid timezone', () => {
      const placeholders = ['user_timezone'];
      const values = {
        user_timezone: 'America/New_York'
      };

      const result = PlaceholderResolver.validateUserInputs(placeholders, values);
      
      expect(result.isValid).toBe(true);
      expect(result.errors).toEqual([]);
    });

    it('handles empty placeholders and values', () => {
      const result = PlaceholderResolver.validateUserInputs([], {});
      
      expect(result.isValid).toBe(true);
      expect(result.errors).toEqual([]);
    });
  });

  describe('getPlaceholderMetadata', () => {
    it('returns metadata for selected_provider placeholder', () => {
      const metadata = PlaceholderResolver.getPlaceholderMetadata('selected_provider');
      
      expect(metadata).toEqual({
        name: 'selected_provider',
        type: 'dropdown',
        description: 'Select the LLM provider for this experience',
        required: true,
        options: null
      });
    });

    it('returns metadata for selected_model placeholder', () => {
      const metadata = PlaceholderResolver.getPlaceholderMetadata('selected_model');
      
      expect(metadata).toEqual({
        name: 'selected_model',
        type: 'dropdown',
        description: 'Select the model to use with the chosen provider',
        required: true,
        options: null
      });
    });

    it('returns metadata for timezone placeholder', () => {
      const metadata = PlaceholderResolver.getPlaceholderMetadata('user_timezone');
      
      expect(metadata).toEqual({
        name: 'user_timezone',
        type: 'timezone',
        description: 'Select your timezone for scheduling',
        required: true,
        options: null
      });
    });

    it('returns metadata for name placeholder', () => {
      const metadata = PlaceholderResolver.getPlaceholderMetadata('experience_name');
      
      expect(metadata).toEqual({
        name: 'experience_name',
        type: 'text',
        description: 'Enter name',
        required: true,
        options: null
      });
    });

    it('returns metadata for trigger_type placeholder', () => {
      const metadata = PlaceholderResolver.getPlaceholderMetadata('trigger_type');
      
      expect(metadata).toEqual({
        name: 'trigger_type',
        type: 'dropdown',
        description: 'How the experience will be triggered',
        required: true,
        options: [
          { label: 'Manual', value: 'manual' },
          { label: 'Cron Schedule', value: 'cron' },
          { label: 'Scheduled', value: 'scheduled' }
        ]
      });
    });

    it('returns metadata for trigger_config placeholder', () => {
      const metadata = PlaceholderResolver.getPlaceholderMetadata('trigger_config');
      
      expect(metadata).toEqual({
        name: 'trigger_config',
        type: 'text',
        description: 'Trigger configuration (e.g., cron expression like "0 7 * * *" for daily at 7 AM)',
        required: true,
        options: null
      });
    });

    it('returns metadata for max_run_seconds placeholder', () => {
      const metadata = PlaceholderResolver.getPlaceholderMetadata('max_run_seconds');
      
      expect(metadata).toEqual({
        name: 'max_run_seconds',
        type: 'number',
        description: 'Maximum runtime in seconds (e.g., 300 for 5 minutes)',
        required: true,
        options: null
      });
    });

    it('returns metadata for cron placeholder', () => {
      const metadata = PlaceholderResolver.getPlaceholderMetadata('cron_schedule');
      
      expect(metadata).toEqual({
        name: 'cron_schedule',
        type: 'text',
        description: 'Enter cron expression (e.g., "0 7 * * *" for 7 AM daily)',
        required: true,
        options: null
      });
    });

    it('returns default metadata for unknown placeholder', () => {
      const metadata = PlaceholderResolver.getPlaceholderMetadata('unknown_placeholder');
      
      expect(metadata).toEqual({
        name: 'unknown_placeholder',
        type: 'text',
        description: 'Value for unknown_placeholder',
        required: true,
        options: null
      });
    });
  });

  describe('getTimezoneOptions', () => {
    it('returns array of timezone options', () => {
      const options = PlaceholderResolver.getTimezoneOptions();
      
      expect(Array.isArray(options)).toBe(true);
      expect(options.length).toBeGreaterThan(0);
      
      // Check structure of first option
      expect(options[0]).toHaveProperty('label');
      expect(options[0]).toHaveProperty('value');
      
      // Check that UTC is included
      const utcOption = options.find(opt => opt.value === 'UTC');
      expect(utcOption).toBeDefined();
      expect(utcOption.label).toBe('UTC');
      
      // Check that underscores are replaced with spaces in labels
      const nyOption = options.find(opt => opt.value === 'America/New_York');
      expect(nyOption).toBeDefined();
      expect(nyOption.label).toBe('America/New York');
    });
  });

  describe('_isValidTimezone', () => {
    it('validates correct timezone strings', () => {
      expect(PlaceholderResolver._isValidTimezone('UTC')).toBe(true);
      expect(PlaceholderResolver._isValidTimezone('America/New_York')).toBe(true);
      expect(PlaceholderResolver._isValidTimezone('Europe/London')).toBe(true);
    });

    it('rejects invalid timezone strings', () => {
      expect(PlaceholderResolver._isValidTimezone('Invalid/Timezone')).toBe(false);
      expect(PlaceholderResolver._isValidTimezone('Not_A_Timezone')).toBe(false);
      expect(PlaceholderResolver._isValidTimezone('')).toBe(false);
    });
  });

});
