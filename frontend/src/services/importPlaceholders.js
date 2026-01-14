/**
 * Import Placeholders Service
 * 
 * Handles the fixed set of placeholders used in YAML import/export.
 * These are different from runtime Jinja2 template variables.
 */

// Fixed set of supported import placeholders
export const SUPPORTED_IMPORT_PLACEHOLDERS = {
    'trigger_type': {
        type: 'dropdown',
        label: 'Trigger Type',
        description: 'How the experience will be triggered',
        required: true,
        options: [
            { value: 'manual', label: 'Manual' },
            { value: 'scheduled', label: 'Scheduled' },
            { value: 'cron', label: 'Cron' }
        ]
    },
    'trigger_config': {
        type: 'dynamic', // Special type that depends on trigger_type
        label: 'Trigger Configuration',
        description: 'Configuration for the selected trigger type',
        required: true
    },
    'model_configuration_id': {
        type: 'model_configuration',
        label: 'Model Configuration',
        description: 'Choose your model configuration for LLM synthesis',
        required: false
    },
    'max_run_seconds': {
        type: 'number',
        label: 'Max Run Time (seconds)',
        description: 'Maximum time the experience is allowed to run',
        required: true,
        min: 10,
        max: 600,
        default: 30
    }
};

/**
 * Extract import placeholders from YAML content
 * Only looks for the specific supported placeholders
 */
export function extractImportPlaceholders(yamlContent) {
    const placeholders = [];
    const placeholderRegex = /\{\{\s*([^}]+)\s*\}\}/g;
    
    let match;
    while ((match = placeholderRegex.exec(yamlContent)) !== null) {
        const placeholder = match[1].trim();
        
        // Only include supported placeholders
        if (SUPPORTED_IMPORT_PLACEHOLDERS[placeholder]) {
            if (!placeholders.includes(placeholder)) {
                placeholders.push(placeholder);
            }
        }
    }
    
    return placeholders;
}

/**
 * Validate placeholder values
 */
export function validatePlaceholderValues(placeholders, values) {
    const errors = {};
    
    for (const placeholder of placeholders) {
        const config = SUPPORTED_IMPORT_PLACEHOLDERS[placeholder];
        if (!config) continue;
        
        const value = values[placeholder];
        
        // Required field validation
        if (config.required && (!value || value.toString().trim() === '')) {
            errors[placeholder] = `${config.label} is required`;
            continue;
        }
        
        // Skip further validation if field is empty and not required
        if (!value || value.toString().trim() === '') {
            continue;
        }
        
        // Type-specific validation
        switch (config.type) {
            case 'number': {
                const num = parseInt(value, 10);
                if (!Number.isFinite(num)) {
                    errors[placeholder] = `${config.label} must be a valid number`;
                } else if (config.min && num < config.min) {
                    errors[placeholder] = `${config.label} must be at least ${config.min}`;
                } else if (config.max && num > config.max) {
                    errors[placeholder] = `${config.label} must be at most ${config.max}`;
                }
                break;
            }
                
            case 'dropdown': {
                const validValues = config.options.map(opt => opt.value);
                if (!validValues.includes(value)) {
                    errors[placeholder] = `${config.label} must be one of: ${validValues.join(', ')}`;
                }
                break;
            }
                
            case 'dynamic': {
                // Special validation for trigger_config based on trigger_type
                if (placeholder === 'trigger_config') {
                    const triggerType = values['trigger_type'];
                    if (triggerType === 'scheduled' && !value.scheduled_at) {
                        errors[placeholder] = 'Scheduled date/time is required';
                    } else if (triggerType === 'cron' && !value.cron) {
                        errors[placeholder] = 'Cron expression is required';
                    }
                }
                break;
            }
        }
    }
    
    return {
        isValid: Object.keys(errors).length === 0,
        errors
    };
}

/**
 * Get default values for placeholders
 */
export function getDefaultPlaceholderValues(placeholders) {
    const defaults = {};
    
    for (const placeholder of placeholders) {
        const config = SUPPORTED_IMPORT_PLACEHOLDERS[placeholder];
        if (config && config.default !== undefined) {
            defaults[placeholder] = config.default;
        }
    }
    
    return defaults;
}

/**
 * Replace placeholders in YAML content with actual values
 * Note: This function strips comment lines to avoid replacing placeholders in comments
 */
export function replacePlaceholders(yamlContent, values) {
    // Remove comment lines to avoid replacing placeholders in comments
    const lines = yamlContent.split('\n');
    const contentLines = lines.filter(line => !line.trim().startsWith('#'));
    let result = contentLines.join('\n');
    
    // Replace placeholders
    for (const [placeholder, value] of Object.entries(values)) {
        if (SUPPORTED_IMPORT_PLACEHOLDERS[placeholder]) {
            // Handle both quoted and unquoted placeholders
            const quotedRegex = new RegExp(`"\\{\\{\\s*${placeholder}\\s*\\}\\}"`, 'g');
            const unquotedRegex = new RegExp(`\\{\\{\\s*${placeholder}\\s*\\}\\}`, 'g');
            
            // Handle different value types
            let replacementValue;
            if (typeof value === 'object' && value !== null) {
                // For objects like trigger_config, convert to proper YAML object
                if (Object.keys(value).length === 0) {
                    replacementValue = '{}';
                } else {
                    // Convert to YAML object format with proper quoting for string values
                    const yamlLines = Object.entries(value).map(([k, v]) => {
                        // Quote string values to prevent YAML parsing issues with special characters
                        const quotedValue = typeof v === 'string' ? `"${v}"` : v;
                        return `  ${k}: ${quotedValue}`;
                    });
                    replacementValue = `\n${yamlLines.join('\n')}`;
                }
            } else if (typeof value === 'string') {
                replacementValue = value; // Don't add extra quotes
            } else {
                replacementValue = String(value);
            }
            
            // Replace both quoted and unquoted versions
            result = result.replace(quotedRegex, replacementValue);
            result = result.replace(unquotedRegex, replacementValue);
        }
    }
    
    return result;
}