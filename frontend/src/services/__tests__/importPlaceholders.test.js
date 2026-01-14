import {
    SUPPORTED_IMPORT_PLACEHOLDERS,
    extractImportPlaceholders,
    validatePlaceholderValues,
    getDefaultPlaceholderValues,
    replacePlaceholders
} from '../importPlaceholders';

describe('importPlaceholders', () => {
    describe('extractImportPlaceholders', () => {
        test('extracts supported placeholders from YAML', () => {
            const yamlContent = `
name: Test Experience
trigger_type: {{ trigger_type }}
model_configuration_id: {{ model_configuration_id }}
max_run_seconds: {{ max_run_seconds }}
unsupported_placeholder: {{ some_random_placeholder }}
inline_prompt_template: "Hello {{ user.name }}, here is {{ step_outputs.gmail }}"
            `;

            const placeholders = extractImportPlaceholders(yamlContent);
            
            expect(placeholders).toEqual([
                'trigger_type',
                'model_configuration_id', 
                'max_run_seconds'
            ]);
            
            // Should not include unsupported placeholders or runtime template variables
            expect(placeholders).not.toContain('some_random_placeholder');
            expect(placeholders).not.toContain('user.name');
            expect(placeholders).not.toContain('step_outputs.gmail');
        });

        test('returns empty array for YAML without placeholders', () => {
            const yamlContent = `
name: Test Experience
description: A simple experience
trigger_type: manual
            `;

            const placeholders = extractImportPlaceholders(yamlContent);
            expect(placeholders).toEqual([]);
        });

        test('handles empty or invalid input', () => {
            expect(extractImportPlaceholders('')).toEqual([]);
            expect(extractImportPlaceholders(null)).toEqual([]);
            expect(extractImportPlaceholders(undefined)).toEqual([]);
        });
    });

    describe('validatePlaceholderValues', () => {
        test('validates required placeholders', () => {
            const placeholders = ['trigger_type', 'max_run_seconds'];
            const values = {
                trigger_type: 'manual',
                // max_run_seconds is missing
            };

            const result = validatePlaceholderValues(placeholders, values);
            
            expect(result.isValid).toBe(false);
            expect(result.errors.max_run_seconds).toContain('required');
        });

        test('validates number types', () => {
            const placeholders = ['max_run_seconds'];
            const values = {
                max_run_seconds: 'not-a-number'
            };

            const result = validatePlaceholderValues(placeholders, values);
            
            expect(result.isValid).toBe(false);
            expect(result.errors.max_run_seconds).toContain('valid number');
        });

        test('validates number ranges', () => {
            const placeholders = ['max_run_seconds'];
            const values = {
                max_run_seconds: '5' // Below minimum of 10
            };

            const result = validatePlaceholderValues(placeholders, values);
            
            expect(result.isValid).toBe(false);
            expect(result.errors.max_run_seconds).toContain('at least 10');
        });

        test('validates dropdown options', () => {
            const placeholders = ['trigger_type'];
            const values = {
                trigger_type: 'invalid_option'
            };

            const result = validatePlaceholderValues(placeholders, values);
            
            expect(result.isValid).toBe(false);
            expect(result.errors.trigger_type).toContain('must be one of');
        });

        test('validates model configuration field', () => {
            const placeholders = ['model_configuration_id'];
            const values = {
                model_configuration_id: 'config-1',
            };

            const result = validatePlaceholderValues(placeholders, values);
            expect(result.isValid).toBe(true);
            expect(result.errors).toEqual({});
        });

        test('passes validation with valid values', () => {
            const placeholders = ['trigger_type', 'max_run_seconds'];
            const values = {
                trigger_type: 'manual',
                max_run_seconds: '120'
            };

            const result = validatePlaceholderValues(placeholders, values);
            
            expect(result.isValid).toBe(true);
            expect(Object.keys(result.errors)).toHaveLength(0);
        });
    });

    describe('getDefaultPlaceholderValues', () => {
        test('returns default values for placeholders that have them', () => {
            const placeholders = ['max_run_seconds', 'trigger_type'];
            const defaults = getDefaultPlaceholderValues(placeholders);
            
            expect(defaults.max_run_seconds).toBe(30);
            expect(defaults.trigger_type).toBeUndefined(); // No default for trigger_type
        });
    });

    describe('replacePlaceholders', () => {
        test('replaces supported placeholders in YAML', () => {
            const yamlContent = `
name: Test Experience
trigger_type: "{{ trigger_type }}"
model_configuration_id: "{{ model_configuration_id }}"
max_run_seconds: "{{ max_run_seconds }}"
unsupported: "{{ unsupported_placeholder }}"
            `;

            const values = {
                trigger_type: 'manual',
                model_configuration_id: 'config-1',
                max_run_seconds: 120,
                unsupported_placeholder: 'should_not_replace'
            };

            const result = replacePlaceholders(yamlContent, values);
            
            expect(result).toContain('trigger_type: manual');
            expect(result).toContain('model_configuration_id: config-1');
            expect(result).toContain('max_run_seconds: 120');
            
            // Unsupported placeholders should remain unchanged
            expect(result).toContain('"{{ unsupported_placeholder }}"');
        });

        test('handles object values like trigger_config', () => {
            const yamlContent = `
trigger_config: "{{ trigger_config }}"
            `;

            const values = {
                trigger_config: { cron: '0 9 * * *' }
            };

            const result = replacePlaceholders(yamlContent, values);
            
            expect(result).toContain('trigger_config: \n  cron: 0 9 * * *');
        });

        test('preserves runtime template variables', () => {
            const yamlContent = `
inline_prompt_template: "Hello {{ user.name }}, here is {{ step_outputs.gmail }}"
trigger_type: {{ trigger_type }}
            `;

            const values = {
                trigger_type: 'manual'
            };

            const result = replacePlaceholders(yamlContent, values);
            
            // Import placeholder should be replaced
            expect(result).toContain('trigger_type: manual');
            
            // Runtime template variables should be preserved
            expect(result).toContain('{{ user.name }}');
            expect(result).toContain('{{ step_outputs.gmail }}');
        });
    });

    describe('SUPPORTED_IMPORT_PLACEHOLDERS', () => {
        test('contains expected placeholder configurations', () => {
            const expectedPlaceholders = [
                'trigger_type',
                'trigger_config', 
                'model_configuration_id',
                'max_run_seconds'
            ];

            expectedPlaceholders.forEach(placeholder => {
                expect(SUPPORTED_IMPORT_PLACEHOLDERS[placeholder]).toBeDefined();
                expect(SUPPORTED_IMPORT_PLACEHOLDERS[placeholder].label).toBeDefined();
                expect(SUPPORTED_IMPORT_PLACEHOLDERS[placeholder].description).toBeDefined();
            });
        });

        test('trigger_type has correct dropdown options', () => {
            const config = SUPPORTED_IMPORT_PLACEHOLDERS.trigger_type;
            
            expect(config.type).toBe('dropdown');
            expect(config.required).toBe(true);
            expect(config.options).toEqual([
                { value: 'manual', label: 'Manual' },
                { value: 'scheduled', label: 'Scheduled' },
                { value: 'cron', label: 'Cron' }
            ]);
        });

        test('max_run_seconds has correct number constraints', () => {
            const config = SUPPORTED_IMPORT_PLACEHOLDERS.max_run_seconds;
            
            expect(config.type).toBe('number');
            expect(config.required).toBe(true);
            expect(config.min).toBe(10);
            expect(config.max).toBe(600);
            expect(config.default).toBe(30);
        });
    });
});