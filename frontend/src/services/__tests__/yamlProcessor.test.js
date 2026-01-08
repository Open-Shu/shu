import YAMLProcessor from '../yamlProcessor';

describe('YAMLProcessor', () => {
  describe('parseYAML', () => {
    it('parses valid YAML correctly', () => {
      const yamlContent = `
name: "Test Experience"
description: "A test experience"
version: 1
steps:
  - step_key: "test"
    step_type: "plugin"
`;
      const result = YAMLProcessor.parseYAML(yamlContent);
      
      expect(result).toEqual({
        name: "Test Experience",
        description: "A test experience",
        version: 1,
        steps: [
          {
            step_key: "test",
            step_type: "plugin"
          }
        ]
      });
    });

    it('throws error for invalid YAML syntax', () => {
      const invalidYaml = `
name: "Test Experience"
description: "A test experience
version: 1
`;
      expect(() => YAMLProcessor.parseYAML(invalidYaml)).toThrow(/Invalid YAML syntax/);
    });

    it('throws error for empty or null input', () => {
      expect(() => YAMLProcessor.parseYAML('')).toThrow('YAML content must be a non-empty string');
      expect(() => YAMLProcessor.parseYAML(null)).toThrow('YAML content must be a non-empty string');
      expect(() => YAMLProcessor.parseYAML(undefined)).toThrow('YAML content must be a non-empty string');
    });

    it('throws error for non-object YAML', () => {
      expect(() => YAMLProcessor.parseYAML('just a string')).toThrow('YAML must contain a valid object structure');
      expect(() => YAMLProcessor.parseYAML('123')).toThrow('YAML must contain a valid object structure');
      expect(() => YAMLProcessor.parseYAML('- item1\n- item2')).toThrow('YAML must contain a valid object structure');
    });
  });

  describe('extractPlaceholders', () => {
    it('extracts placeholders from YAML content', () => {
      const yamlContent = `
name: "{{ experience_name }}"
description: "Daily briefing for {{ user_name }}"
llm_provider_id: "{{ selected_provider }}"
model_name: "{{ selected_model }}"
trigger_config:
  timezone: "{{ user_timezone }}"
`;
      const placeholders = YAMLProcessor.extractPlaceholders(yamlContent);
      
      expect(placeholders).toEqual([
        'experience_name',
        'selected_model',
        'selected_provider',
        'user_name',
        'user_timezone'
      ]);
    });

    it('handles placeholders with whitespace', () => {
      const yamlContent = `
name: "{{  experience_name  }}"
description: "{{ user_name}}"
provider: "{{selected_provider }}"
`;
      const placeholders = YAMLProcessor.extractPlaceholders(yamlContent);
      
      expect(placeholders).toEqual([
        'experience_name',
        'selected_provider',
        'user_name'
      ]);
    });

    it('returns empty array for content without placeholders', () => {
      const yamlContent = `
name: "Static Experience"
description: "No placeholders here"
`;
      const placeholders = YAMLProcessor.extractPlaceholders(yamlContent);
      
      expect(placeholders).toEqual([]);
    });

    it('returns empty array for empty or null input', () => {
      expect(YAMLProcessor.extractPlaceholders('')).toEqual([]);
      expect(YAMLProcessor.extractPlaceholders(null)).toEqual([]);
      expect(YAMLProcessor.extractPlaceholders(undefined)).toEqual([]);
    });

    it('handles duplicate placeholders', () => {
      const yamlContent = `
name: "{{ user_name }}"
description: "Hello {{ user_name }}, welcome!"
`;
      const placeholders = YAMLProcessor.extractPlaceholders(yamlContent);
      
      expect(placeholders).toEqual(['user_name']);
    });
  });

  describe('resolvePlaceholders', () => {
    it('resolves placeholders with provided values', () => {
      const yamlContent = `
name: "{{ experience_name }}"
description: "Daily briefing for {{ user_name }}"
llm_provider_id: "{{ selected_provider }}"
`;
      const values = {
        experience_name: 'Morning Briefing',
        user_name: 'John Doe',
        selected_provider: 'openai'
      };
      
      const resolved = YAMLProcessor.resolvePlaceholders(yamlContent, values);
      
      expect(resolved).toContain('name: "Morning Briefing"');
      expect(resolved).toContain('description: "Daily briefing for John Doe"');
      expect(resolved).toContain('llm_provider_id: "openai"');
    });

    it('handles missing values gracefully', () => {
      const yamlContent = `
name: "{{ experience_name }}"
description: "{{ missing_value }}"
`;
      const values = {
        experience_name: 'Test Experience'
      };
      
      const resolved = YAMLProcessor.resolvePlaceholders(yamlContent, values);
      
      expect(resolved).toContain('name: "Test Experience"');
      expect(resolved).toContain('description: "{{ missing_value }}"');
    });

    it('handles non-string values', () => {
      const yamlContent = `
version: {{ version_number }}
enabled: {{ is_enabled }}
`;
      const values = {
        version_number: 1,
        is_enabled: true
      };
      
      const resolved = YAMLProcessor.resolvePlaceholders(yamlContent, values);
      
      expect(resolved).toContain('version: 1');
      expect(resolved).toContain('enabled: true');
    });

    it('returns original content for empty or null input', () => {
      expect(YAMLProcessor.resolvePlaceholders('', {})).toBe('');
      expect(YAMLProcessor.resolvePlaceholders(null, {})).toBe(null);
      expect(YAMLProcessor.resolvePlaceholders('test', null)).toBe('test');
    });
  });

  describe('convertToExperiencePayload', () => {
    it('converts valid YAML to experience payload', () => {
      const yamlContent = `
name: "Morning Briefing"
description: "Daily summary of emails and calendar"
version: 1
visibility: "published"
trigger_type: "cron"
trigger_config:
  cron: "0 7 * * *"
  timezone: "America/New_York"
include_previous_run: true
llm_provider_id: "openai"
model_name: "gpt-4"
inline_prompt_template: "Summarize the following..."
steps:
  - step_key: "emails"
    step_type: "plugin"
    plugin_name: "gmail"
`;
      
      const payload = YAMLProcessor.convertToExperiencePayload(yamlContent);
      
      expect(payload).toEqual({
        name: "Morning Briefing",
        description: "Daily summary of emails and calendar",
        version: 1,
        visibility: "published",
        trigger_type: "cron",
        trigger_config: {
          cron: "0 7 * * *",
          timezone: "America/New_York"
        },
        include_previous_run: true,
        llm_provider_id: "openai",
        model_name: "gpt-4",
        inline_prompt_template: "Summarize the following...",
        steps: [
          {
            step_key: "emails",
            step_type: "plugin",
            plugin_name: "gmail"
          }
        ]
      });
    });

    it('applies default values for optional fields', () => {
      const yamlContent = `
name: "Simple Experience"
description: "A simple test experience"
`;
      
      const payload = YAMLProcessor.convertToExperiencePayload(yamlContent);
      
      expect(payload.version).toBe(1);
      expect(payload.visibility).toBe('draft');
      expect(payload.trigger_type).toBe('manual');
      expect(payload.trigger_config).toEqual({});
      expect(payload.include_previous_run).toBe(false);
      expect(payload.inline_prompt_template).toBe('');
      expect(payload.steps).toEqual([]);
    });

    it('throws error for missing required fields', () => {
      const yamlContent = `
name: "Test Experience"
`;
      
      expect(() => YAMLProcessor.convertToExperiencePayload(yamlContent))
        .toThrow('Missing required fields: description');
    });

    it('throws error for invalid steps format', () => {
      const yamlContent = `
name: "Test Experience"
description: "Test description"
steps: "not an array"
`;
      
      expect(() => YAMLProcessor.convertToExperiencePayload(yamlContent))
        .toThrow('Steps must be an array');
    });
  });

  describe('validateExperienceYAML', () => {
    it('validates correct YAML structure', () => {
      const yamlContent = `
name: "Test Experience"
description: "A test experience"
steps:
  - step_key: "test"
    step_type: "plugin"
`;
      
      const result = YAMLProcessor.validateExperienceYAML(yamlContent);
      
      expect(result.isValid).toBe(true);
      expect(result.errors).toEqual([]);
    });

    it('validates cron trigger configuration', () => {
      const yamlContent = `
name: "Test Experience"
description: "A test experience"
trigger_type: "cron"
trigger_config:
  timezone: "America/New_York"
`;
      
      const result = YAMLProcessor.validateExperienceYAML(yamlContent);
      
      expect(result.isValid).toBe(false);
      expect(result.errors).toContain('Cron trigger requires cron expression in trigger_config');
    });

    it('validates steps structure', () => {
      const yamlContent = `
name: "Test Experience"
description: "A test experience"
steps:
  - step_type: "plugin"
  - step_key: "test"
`;
      
      const result = YAMLProcessor.validateExperienceYAML(yamlContent);
      
      expect(result.isValid).toBe(false);
      expect(result.errors).toContain('Step 1 is missing step_key');
      expect(result.errors).toContain('Step 2 is missing step_type');
    });

    it('handles invalid YAML syntax', () => {
      const yamlContent = `
name: "Test Experience"
description: "Invalid YAML
`;
      
      const result = YAMLProcessor.validateExperienceYAML(yamlContent);
      
      expect(result.isValid).toBe(false);
      expect(result.errors.length).toBeGreaterThan(0);
      expect(result.errors[0]).toMatch(/Invalid YAML syntax/);
    });
  });
});
