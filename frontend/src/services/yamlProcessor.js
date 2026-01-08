import yaml from 'js-yaml';

/**
 * YAMLProcessor service for handling YAML parsing, placeholder extraction,
 * and conversion to experience payload format.
 */
class YAMLProcessor {
  /**
   * Parse and validate YAML content using js-yaml library
   * @param {string} yamlContent - The YAML string to parse
   * @returns {Object} Parsed YAML object
   * @throws {Error} If YAML is invalid with descriptive error message
   */
  parseYAML(yamlContent) {
    if (!yamlContent || typeof yamlContent !== 'string') {
      throw new Error('YAML content must be a non-empty string');
    }

    try {
      const parsed = yaml.load(yamlContent);
      
      // Validate that we got an object (not null, string, number, etc.)
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('YAML must contain a valid object structure');
      }

      return parsed;
    } catch (error) {
      if (error.name === 'YAMLException') {
        // Enhance YAML parsing errors with line number information
        const lineInfo = error.mark ? ` at line ${error.mark.line + 1}, column ${error.mark.column + 1}` : '';
        throw new Error(`Invalid YAML syntax${lineInfo}: ${error.reason || error.message}`);
      }
      throw error;
    }
  }

  /**
   * Extract placeholder patterns from YAML content
   * Finds all {{ variable_name }} patterns in the YAML string
   * @param {string} yamlContent - The YAML string to search
   * @returns {Array<string>} Array of unique placeholder names (without {{ }})
   */
  extractPlaceholders(yamlContent) {
    if (!yamlContent || typeof yamlContent !== 'string') {
      return [];
    }

    // Regular expression to match {{ variable_name }} patterns
    // Allows alphanumeric characters, underscores, and hyphens in variable names
    const placeholderRegex = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*\}\}/g;
    const placeholders = new Set();
    let match;

    while ((match = placeholderRegex.exec(yamlContent)) !== null) {
      // Extract the variable name (group 1) and trim whitespace
      const placeholderName = match[1].trim();
      placeholders.add(placeholderName);
    }

    return Array.from(placeholders).sort();
  }

  /**
   * Resolve placeholders in YAML content by substituting with provided values
   * @param {string} yamlContent - The YAML string containing placeholders
   * @param {Object} values - Object mapping placeholder names to their values
   * @returns {string} YAML content with placeholders substituted
   */
  resolvePlaceholders(yamlContent, values = {}) {
    if (!yamlContent || typeof yamlContent !== 'string') {
      return yamlContent;
    }

    if (!values || typeof values !== 'object') {
      return yamlContent;
    }

    let resolvedContent = yamlContent;

    // Replace each placeholder with its corresponding value
    Object.entries(values).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        // Escape regex special characters in the key
        const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        // Create regex to match the specific placeholder with optional whitespace
        const placeholderRegex = new RegExp(`\\{\\{\\s*${escapedKey}\\s*\\}\\}`, 'g');
        
        // Convert value to string for substitution
        const stringValue = typeof value === 'string' ? value : String(value);
        resolvedContent = resolvedContent.replace(placeholderRegex, stringValue);
      }
    });

    return resolvedContent;
  }

  /**
   * Convert resolved YAML to experience payload format for the API
   * @param {string} resolvedYAML - YAML content with all placeholders resolved
   * @returns {Object} Experience payload object ready for POST /api/v1/experiences
   * @throws {Error} If YAML cannot be parsed or converted
   */
  convertToExperiencePayload(resolvedYAML) {
    // First parse the resolved YAML
    const yamlData = this.parseYAML(resolvedYAML);

    // Validate required fields for experience creation
    const requiredFields = ['name', 'description'];
    const missingFields = requiredFields.filter(field => !yamlData[field]);
    
    if (missingFields.length > 0) {
      throw new Error(`Missing required fields: ${missingFields.join(', ')}`);
    }

    // Convert YAML structure to experience API payload format
    const payload = {
      name: yamlData.name,
      description: yamlData.description,
      version: yamlData.version || 1,
      visibility: yamlData.visibility || 'draft',
      trigger_type: yamlData.trigger_type || 'manual',
      trigger_config: yamlData.trigger_config || {},
      include_previous_run: yamlData.include_previous_run || false,
      llm_provider_id: yamlData.llm_provider_id,
      model_name: yamlData.model_name,
      inline_prompt_template: yamlData.inline_prompt_template || '',
      steps: yamlData.steps || []
    };

    // Validate that steps is an array if provided
    if (payload.steps && !Array.isArray(payload.steps)) {
      throw new Error('Steps must be an array');
    }

    // Validate each step has required fields
    if (payload.steps.length > 0) {
      payload.steps.forEach((step, index) => {
        const missingFields = [];
        if (!step.step_key) missingFields.push('step_key');
        if (!step.step_type) missingFields.push('step_type');
        
        if (missingFields.length > 0) {
          const fieldList = missingFields.length === 1 
            ? `missing required field: ${missingFields[0]}`
            : `missing required fields: ${missingFields.join(', ')}`;
          throw new Error(`Step ${index + 1} ${fieldList}`);
        }
      });
    }

    return payload;
  }

  /**
   * Validate that a YAML string contains valid experience structure
   * @param {string} yamlContent - The YAML string to validate
   * @returns {Object} Validation result with isValid boolean and errors array
   */
  validateExperienceYAML(yamlContent) {
    const result = {
      isValid: true,
      errors: []
    };

    try {
      // Try to parse the YAML
      const parsed = this.parseYAML(yamlContent);
      
      // Check for required top-level fields
      const requiredFields = ['name', 'description'];
      requiredFields.forEach(field => {
        if (!parsed[field]) {
          result.errors.push(`Missing required field: ${field}`);
        }
      });

      // Validate steps structure if present
      if (parsed.steps) {
        if (!Array.isArray(parsed.steps)) {
          result.errors.push('Steps must be an array');
        } else {
          parsed.steps.forEach((step, index) => {
            if (!step.step_key) {
              result.errors.push(`Step ${index + 1} is missing step_key`);
            }
            if (!step.step_type) {
              result.errors.push(`Step ${index + 1} is missing step_type`);
            }
          });
        }
      }

      // Validate trigger_config if trigger_type is cron
      if (parsed.trigger_type === 'cron' && parsed.trigger_config) {
        if (!parsed.trigger_config.cron) {
          result.errors.push('Cron trigger requires cron expression in trigger_config');
        }
      }

    } catch (error) {
      result.errors.push(error.message);
    }

    result.isValid = result.errors.length === 0;
    return result;
  }
}

// Export a singleton instance
export default new YAMLProcessor();
