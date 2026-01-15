import yaml from 'js-yaml';

/**
 * YAMLProcessor service for handling YAML parsing, validation,
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
        // Enhanced YAML parsing errors with detailed line number information
        const lineInfo = error.mark ? ` at line ${error.mark.line + 1}, column ${error.mark.column + 1}` : '';
        const contextInfo = this._getYAMLErrorContext(yamlContent, error.mark);
        const enhancedMessage = `Invalid YAML syntax${lineInfo}: ${error.reason || error.message}${contextInfo}`;
        
        // Create enhanced error with additional properties for UI display
        const enhancedError = new Error(enhancedMessage);
        enhancedError.name = 'YAMLParsingError';
        enhancedError.line = error.mark ? error.mark.line + 1 : null;
        enhancedError.column = error.mark ? error.mark.column + 1 : null;
        enhancedError.reason = error.reason || error.message;
        enhancedError.context = contextInfo;
        
        throw enhancedError;
      }
      throw error;
    }
  }

  /**
   * Get contextual information around a YAML parsing error
   * @private
   * @param {string} yamlContent - The original YAML content
   * @param {Object} mark - Error mark from js-yaml with line/column info
   * @returns {string} Contextual error information
   */
  _getYAMLErrorContext(yamlContent, mark) {
    if (!mark || !yamlContent) return '';
    
    const lines = yamlContent.split('\n');
    const errorLine = mark.line;
    const errorColumn = mark.column;
    
    if (errorLine < 0 || errorLine >= lines.length) return '';
    
    const contextLines = [];
    const startLine = Math.max(0, errorLine - 1);
    const endLine = Math.min(lines.length - 1, errorLine + 1);
    
    for (let i = startLine; i <= endLine; i++) {
      const lineNumber = i + 1;
      const lineContent = lines[i];
      const isErrorLine = i === errorLine;
      
      if (isErrorLine) {
        contextLines.push(`${lineNumber}: ${lineContent}`);
        // Add pointer to error column
        const pointer = ' '.repeat(String(lineNumber).length + 2 + errorColumn) + '^';
        contextLines.push(pointer);
      } else {
        contextLines.push(`${lineNumber}: ${lineContent}`);
      }
    }
    
    return contextLines.length > 0 ? `\n\nContext:\n${contextLines.join('\n')}` : '';
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
      inline_prompt_template: yamlData.inline_prompt_template || '',
      steps: yamlData.steps || []
    };

    // Do not include these in the payload if they are blank or placeholders
    if (typeof yamlData.model_configuration_id === 'string' && yamlData.model_configuration_id.trim() !== '' && !yamlData.model_configuration_id.trim().startsWith("{{")) {
      payload.model_configuration_id = yamlData.model_configuration_id;
    }

    // TODO: Remove empty payload items from the dict so we don't send it to the backend.

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
      errors: [],
      warnings: [],
      pluginValidation: {
        requiredPlugins: [],
        missingPlugins: [],
        hasPluginErrors: false
      }
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
          const pluginValidation = this._validatePluginSteps(parsed.steps);
          result.pluginValidation = pluginValidation;
          
          if (pluginValidation.hasPluginErrors) {
            result.errors.push(...pluginValidation.errors || []);
          }
          
          if (pluginValidation.warnings && pluginValidation.warnings.length > 0) {
            result.warnings.push(...pluginValidation.warnings);
          }

          parsed.steps.forEach((step, index) => {
            if (!step.step_key) {
              result.errors.push(`Step ${index + 1} is missing step_key`);
            }
            if (!step.step_type) {
              result.errors.push(`Step ${index + 1} is missing step_type`);
            }
            
            // Validate plugin steps have required fields
            if (step.step_type === 'plugin') {
              if (!step.plugin_name) {
                result.errors.push(`Plugin step ${index + 1} is missing plugin_name`);
              }
              if (!step.plugin_op) {
                result.errors.push(`Plugin step ${index + 1} is missing plugin_op`);
              }
            }
          });
        }
      }

      // Validate trigger_config if trigger_type is cron
      if (parsed.trigger_type === 'cron' && parsed.trigger_config) {
        if (!parsed.trigger_config.cron) {
          result.errors.push('Cron trigger requires cron expression in trigger_config');
        } else {
          // Basic cron expression validation
          const cronValidation = this._validateCronExpression(parsed.trigger_config.cron);
          if (!cronValidation.isValid) {
            result.errors.push(`Invalid cron expression: ${cronValidation.error}`);
          }
        }
      }

      // Model configuration validation - no cross-field validation needed
      // Model configuration is self-contained

    } catch (error) {
      if (error.name === 'YAMLParsingError') {
        // Enhanced YAML parsing error with line/column info
        result.errors.push({
          type: 'yaml_parsing',
          message: error.message,
          line: error.line,
          column: error.column,
          reason: error.reason,
          context: error.context
        });
      } else {
        result.errors.push(error.message);
      }
    }

    result.isValid = result.errors.length === 0;
    return result;
  }

  /**
   * Validate plugin steps and check for required plugins
   * @private
   * @param {Array} steps - Array of step objects
   * @returns {Object} Plugin validation result
   */
  _validatePluginSteps(steps) {
    const result = {
      requiredPlugins: [],
      missingPlugins: [],
      hasPluginErrors: false,
      errors: [],
      warnings: []
    };

    const pluginSteps = steps.filter(step => step.step_type === 'plugin');
    const requiredPlugins = new Set();

    pluginSteps.forEach((step, index) => {
      if (step.plugin_name) {
        requiredPlugins.add(step.plugin_name);
        
        // TODO: Improve this later, we should load the plugin definitions for an actual validation

        // Validate common plugin operations
        if (step.plugin_op) {
          const commonOps = ['list', 'get', 'create', 'update', 'delete', 'search'];
          if (!commonOps.includes(step.plugin_op)) {
            result.warnings.push(`Step ${index + 1}: Uncommon plugin operation '${step.plugin_op}' for plugin '${step.plugin_name}'`);
          }
        }
      }
    });

    result.requiredPlugins = Array.from(requiredPlugins);

    // TODO: We can check for missing plugins later. Right now this is a hands on activity anyway, so we will skip this for now.
    result.missingPlugins = [];

    return result;
  }

  /**
   * Basic cron expression validation
   * @private
   * @param {string} cronExpression - Cron expression to validate
   * @returns {Object} Validation result
   */
  _validateCronExpression(cronExpression) {
    if (!cronExpression || typeof cronExpression !== 'string') {
      return { isValid: false, error: 'Cron expression must be a non-empty string' };
    }

    const parts = cronExpression.trim().split(/\s+/);
    
    // Standard cron has 5 parts: minute hour day month weekday
    // Some systems support 6 parts with seconds: second minute hour day month weekday
    if (parts.length !== 5 && parts.length !== 6) {
      return { 
        isValid: false, 
        error: `Cron expression must have 5 or 6 parts, got ${parts.length}. Example: "0 7 * * *" for daily at 7 AM` 
      };
    }

    // Basic validation of each part (simplified)
    const validations = [
      { name: 'second', range: [0, 59], optional: true },
      { name: 'minute', range: [0, 59] },
      { name: 'hour', range: [0, 23] },
      { name: 'day', range: [1, 31] },
      { name: 'month', range: [1, 12] },
      { name: 'weekday', range: [0, 7] } // 0 and 7 both represent Sunday
    ];

    const startIndex = parts.length === 6 ? 0 : 1; // Skip seconds validation if not present
    
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const validation = validations[startIndex + i];
      
      if (!validation) continue;
      
      // Allow wildcards and basic expressions
      if (part === '*' || part === '?' || part.includes('/') || part.includes('-') || part.includes(',')) {
        continue;
      }
      
      // Check if it's a valid number within range
      const num = parseInt(part, 10);
      if (isNaN(num) || num < validation.range[0] || num > validation.range[1]) {
        return { 
          isValid: false, 
          error: `Invalid ${validation.name} value: ${part}. Must be between ${validation.range[0]} and ${validation.range[1]}` 
        };
      }
    }

    return { isValid: true };
  }
}

// Export a singleton instance
export default new YAMLProcessor();
