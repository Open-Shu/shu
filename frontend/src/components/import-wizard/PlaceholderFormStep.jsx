import React, { useState, useEffect, useCallback } from "react";
import { Box, Typography, TextField, Alert, Paper, Grid } from "@mui/material";
import {
  SUPPORTED_IMPORT_PLACEHOLDERS,
  validatePlaceholderValues,
  getDefaultPlaceholderValues,
} from "../../services/importPlaceholders";
import { log } from "../../utils/log";
import TriggerConfiguration from "../shared/TriggerConfiguration";
import ModelConfigurationSelector from "../shared/ModelConfigurationSelector";

/**
 * PlaceholderFormStep - Second step of the import wizard for filling placeholder values
 *
 * @param {Object} props - Component props
 * @param {Array<string>} props.placeholders - Array of placeholder names extracted from YAML
 * @param {Object} props.values - Current placeholder values
 * @param {function} props.onValuesChange - Callback when placeholder values change
 * @param {function} props.onValidationChange - Callback when validation state changes
 */
const PlaceholderFormStep = ({
  placeholders = [],
  values = {},
  onValuesChange,
  onValidationChange,
}) => {
  const [validationErrors, setValidationErrors] = useState({});

  // Initialize with default values
  useEffect(() => {
    const defaults = getDefaultPlaceholderValues(placeholders);
    if (Object.keys(defaults).length > 0) {
      const newValues = { ...defaults, ...values };
      onValuesChange(newValues);
      log.debug("Applied default placeholder values", defaults);
    }
  }, [placeholders]); // Only run when placeholders change

  // Validate placeholder values whenever they change
  const validatePlaceholders = useCallback(
    (currentValues) => {
      try {
        const validation = validatePlaceholderValues(
          placeholders,
          currentValues,
        );
        setValidationErrors(validation.errors);
        onValidationChange(validation.isValid);
        return validation.isValid;
      } catch (error) {
        log.error("Placeholder validation error", { error: error.message });
        setValidationErrors({ _general: "Validation error occurred" });
        onValidationChange(false);
        return false;
      }
    },
    [placeholders, onValidationChange],
  );

  // Validate whenever values change
  useEffect(() => {
    validatePlaceholders(values);
  }, [values, validatePlaceholders]);

  // Handle value changes for individual placeholders
  const handleValueChange = useCallback(
    (placeholderName, newValue) => {
      onValuesChange((prevValues) => ({
        ...prevValues,
        [placeholderName]: newValue,
      }));
    },
    [onValuesChange],
  );

  // Render form field for a placeholder
  const renderPlaceholderField = (placeholderName) => {
    const config = SUPPORTED_IMPORT_PLACEHOLDERS[placeholderName];
    if (!config) {
      log.warn(`Unsupported placeholder: ${placeholderName}`);
      return null;
    }

    const currentValue = values[placeholderName] || "";
    const hasError = !!validationErrors[placeholderName];
    const errorMessage = validationErrors[placeholderName];

    // Trigger Type dropdown
    if (placeholderName === "trigger_type") {
      return (
        <TriggerConfiguration
          key={placeholderName}
          triggerType={currentValue}
          triggerConfig={values["trigger_config"] || {}}
          onTriggerTypeChange={(newType) =>
            handleValueChange("trigger_type", newType)
          }
          onTriggerConfigChange={(newConfig) =>
            handleValueChange("trigger_config", newConfig)
          }
          validationErrors={validationErrors}
          required={config.required}
          showHelperText={true}
        />
      );
    }

    // Skip trigger_config as it's handled by TriggerConfiguration
    if (placeholderName === "trigger_config") {
      return null;
    }

    // Model Configuration selection
    if (placeholderName === "model_configuration_id") {
      return (
        <ModelConfigurationSelector
          key={placeholderName}
          modelConfigurationId={currentValue}
          onModelConfigurationChange={(newValue) =>
            handleValueChange("model_configuration_id", newValue)
          }
          validationErrors={validationErrors}
          required={config.required}
          label={config.label}
          showHelperText={true}
        />
      );
    }

    // Number input
    if (config.type === "number") {
      return (
        <TextField
          key={placeholderName}
          fullWidth
          type="number"
          label={`${config.label} ${config.required ? "*" : ""}`}
          value={currentValue}
          onChange={(e) => handleValueChange(placeholderName, e.target.value)}
          error={hasError}
          helperText={hasError ? errorMessage : config.description}
          inputProps={{
            min: config.min || 0,
            max: config.max || undefined,
          }}
        />
      );
    }

    // Default text input
    return (
      <TextField
        key={placeholderName}
        fullWidth
        label={`${config.label} ${config.required ? "*" : ""}`}
        value={currentValue}
        onChange={(e) => handleValueChange(placeholderName, e.target.value)}
        error={hasError}
        helperText={hasError ? errorMessage : config.description}
      />
    );
  };

  // Filter out unsupported placeholders and group related ones
  const supportedPlaceholders = placeholders.filter(
    (p) => SUPPORTED_IMPORT_PLACEHOLDERS[p],
  );
  const unsupportedPlaceholders = placeholders.filter(
    (p) => !SUPPORTED_IMPORT_PLACEHOLDERS[p],
  );

  // Group related placeholders to avoid duplicates in rendering
  const groupedPlaceholders = [];
  const processedPlaceholders = new Set();

  supportedPlaceholders.forEach((placeholder) => {
    if (processedPlaceholders.has(placeholder)) return;

    if (placeholder === "trigger_type") {
      groupedPlaceholders.push("trigger_type");
      processedPlaceholders.add("trigger_type");
      // Only mark trigger_config as processed if it's actually in the placeholders
      if (supportedPlaceholders.includes("trigger_config")) {
        processedPlaceholders.add("trigger_config");
      }
    } else if (placeholder === "model_configuration_id") {
      groupedPlaceholders.push("model_configuration_id");
      processedPlaceholders.add("model_configuration_id");
    } else if (!processedPlaceholders.has(placeholder)) {
      groupedPlaceholders.push(placeholder);
      processedPlaceholders.add(placeholder);
    }
  });

  // If no supported placeholders, show success message
  if (groupedPlaceholders.length === 0) {
    return (
      <Box>
        <Typography variant="h6" gutterBottom>
          Configuration Complete
        </Typography>

        <Alert severity="success" sx={{ mb: 2 }}>
          <Typography variant="body2">
            No configuration placeholders found in the YAML. The experience is
            ready to be created as-is.
          </Typography>
        </Alert>

        {unsupportedPlaceholders.length > 0 && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            <Typography variant="body2" gutterBottom>
              <strong>Note:</strong> The following placeholders were found but
              are not supported for import:
            </Typography>
            <Box component="ul" sx={{ m: 0, pl: 2 }}>
              {unsupportedPlaceholders.map((p) => (
                <Typography key={p} component="li" variant="body2">
                  {p}
                </Typography>
              ))}
            </Box>
            <Typography variant="body2" sx={{ mt: 1 }}>
              These will be left as-is in the imported experience and can be
              configured after import.
            </Typography>
          </Alert>
        )}

        <Typography variant="body2" color="text.secondary">
          You can proceed to the next step to create the experience.
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h6" gutterBottom>
        Configure Import Settings
      </Typography>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Fill in the configuration values for your imported experience. These
        settings will replace the placeholders in the YAML.
      </Typography>

      {/* Show warning for unsupported placeholders */}
      {unsupportedPlaceholders.length > 0 && (
        <Alert severity="info" sx={{ mb: 3 }}>
          <Typography variant="body2" gutterBottom>
            <strong>Note:</strong> Some placeholders in your YAML are not
            configurable during import:
          </Typography>
          <Box component="ul" sx={{ m: 0, pl: 2 }}>
            {unsupportedPlaceholders.map((p) => (
              <Typography key={p} component="li" variant="body2">
                {p}
              </Typography>
            ))}
          </Box>
          <Typography variant="body2" sx={{ mt: 1 }}>
            These will remain as template variables and can be configured after
            import.
          </Typography>
        </Alert>
      )}

      {/* General validation errors */}
      {validationErrors._general && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {validationErrors._general}
        </Alert>
      )}

      {/* Form fields */}
      <Paper variant="outlined" sx={{ p: 3 }}>
        <Grid container spacing={3}>
          {groupedPlaceholders.map((placeholder) => {
            const renderedField = renderPlaceholderField(placeholder);
            return renderedField ? (
              <Grid item xs={12} key={placeholder}>
                {renderedField}
              </Grid>
            ) : null;
          })}
        </Grid>
      </Paper>

      {/* Help text */}
      <Alert severity="info" sx={{ mt: 3 }}>
        <Typography variant="body2">
          <strong>Tips:</strong>
        </Typography>
        <Box component="ul" sx={{ m: 0, mt: 1, pl: 2 }}>
          <Typography component="li" variant="body2">
            Fields marked with * are required
          </Typography>
          <Typography component="li" variant="body2">
            Model configuration is optional - leave blank to create experience
            without AI processing
          </Typography>
          <Typography component="li" variant="body2">
            Trigger configuration determines when the experience runs
            automatically
          </Typography>
          <Typography component="li" variant="body2">
            The maximum runtime dictates how long the experience is allowed to
            run before automatically interrupting
          </Typography>
        </Box>
      </Alert>
    </Box>
  );
};

export default PlaceholderFormStep;
