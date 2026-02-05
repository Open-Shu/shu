import React, { useState, useCallback, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Stepper,
  Step,
  StepLabel,
  Box,
  Typography,
  Alert,
} from '@mui/material';
import { Close as CloseIcon } from '@mui/icons-material';
import { extractImportPlaceholders } from '../services/importPlaceholders';
import { log } from '../utils/log';

// Import wizard step components
import YAMLInputStep from './import-wizard/YAMLInputStep';
import PlaceholderFormStep from './import-wizard/PlaceholderFormStep';
import ExperienceCreationStep from './import-wizard/ExperienceCreationStep';

// Wizard step definitions
const STEPS = [
  {
    key: 'yaml-input',
    label: 'YAML Input',
    description: 'Provide or edit YAML configuration',
  },
  {
    key: 'placeholder-form',
    label: 'Configure Values',
    description: 'Fill in placeholder values',
  },
  {
    key: 'creation',
    label: 'Create Experience',
    description: 'Review and create the experience',
  },
];

/**
 * ImportExperienceWizard - Multi-step wizard for importing experiences from YAML
 *
 * @param {Object} props - Component props
 * @param {boolean} props.open - Whether the wizard dialog is open
 * @param {function} props.onClose - Callback when wizard is closed
 * @param {string} [props.prePopulatedYAML] - Pre-populated YAML content (e.g., from Quick Start)
 * @param {function} [props.onSuccess] - Callback when experience is successfully created
 */
const ImportExperienceWizard = ({ open, onClose, prePopulatedYAML = null, onSuccess }) => {
  // Wizard state management
  const [currentStep, setCurrentStep] = useState(0);
  const [yamlContent, setYAMLContent] = useState(prePopulatedYAML || '');
  const [extractedPlaceholders, setExtractedPlaceholders] = useState([]);
  const [placeholderValues, setPlaceholderValues] = useState({});
  const [validationState, setValidationState] = useState({
    yamlValid: false,
    placeholdersValid: false,
  });
  const [wizardError, setWizardError] = useState(null);

  // Initialize YAML content when prePopulatedYAML changes
  useEffect(() => {
    if (prePopulatedYAML && prePopulatedYAML !== yamlContent) {
      setYAMLContent(prePopulatedYAML);
      // Reset wizard state when new YAML is provided
      setCurrentStep(0);
      setExtractedPlaceholders([]);
      setPlaceholderValues({});
      setValidationState({ yamlValid: false, placeholdersValid: false });
      setWizardError(null);
    }
  }, [prePopulatedYAML, yamlContent]);

  // Handle YAML content changes
  const handleYAMLChange = useCallback((newYAMLContent) => {
    setYAMLContent(newYAMLContent);
    setWizardError(null);

    try {
      // Extract import placeholders from the new YAML content (only supported ones)
      const placeholders = extractImportPlaceholders(newYAMLContent);
      setExtractedPlaceholders(placeholders);

      // Reset placeholder values when YAML changes
      setPlaceholderValues({});
      setValidationState((prev) => ({ ...prev, placeholdersValid: false }));

      log.debug('Extracted import placeholders from YAML', { placeholders });
    } catch (error) {
      log.warn('Failed to extract placeholders', { error: error.message });
      setExtractedPlaceholders([]);
    }
  }, []);

  // Handle placeholder values changes
  const handlePlaceholderValuesChange = useCallback((newValues) => {
    setPlaceholderValues(newValues);
    setWizardError(null);
  }, []);

  // Handle validation state changes
  const handleValidationChange = useCallback((stepKey, isValid) => {
    setValidationState((prev) => ({
      ...prev,
      [`${stepKey}Valid`]: isValid,
    }));
  }, []);

  // Handle navigation
  const handleNext = useCallback(() => {
    if (currentStep < STEPS.length - 1) {
      setCurrentStep((prev) => prev + 1);
    }
  }, [currentStep]);

  const handleBack = useCallback(() => {
    if (currentStep > 0) {
      setCurrentStep((prev) => prev - 1);
    }
  }, [currentStep]);

  // Close handler
  const handleClose = useCallback(() => {
    // Reset wizard state when closing
    setCurrentStep(0);
    setYAMLContent(prePopulatedYAML || '');
    setExtractedPlaceholders([]);
    setPlaceholderValues({});
    setValidationState({ yamlValid: false, placeholdersValid: false });
    setWizardError(null);

    if (onClose) {
      onClose();
    }
  }, [prePopulatedYAML, onClose]);

  // Determine if next button should be enabled
  const canProceed = () => {
    switch (currentStep) {
      case 0: // YAML Input step
        return validationState.yamlValid && yamlContent.trim() !== '';
      case 1: // Placeholder Form step
        return extractedPlaceholders.length === 0 || validationState.placeholdersValid;
      case 2: // Creation step - hide next button on final step
        return false;
      default:
        return false;
    }
  };

  // Get button text for current step
  const getNextButtonText = () => {
    return 'Next';
  };

  // Render current step content
  const renderStepContent = () => {
    switch (currentStep) {
      case 0:
        return (
          <YAMLInputStep
            yamlContent={yamlContent}
            onYAMLChange={handleYAMLChange}
            onValidationChange={(isValid) => handleValidationChange('yaml', isValid)}
            prePopulatedYAML={prePopulatedYAML}
          />
        );
      case 1:
        return (
          <PlaceholderFormStep
            placeholders={extractedPlaceholders}
            values={placeholderValues}
            onValuesChange={handlePlaceholderValuesChange}
            onValidationChange={(isValid) => handleValidationChange('placeholders', isValid)}
          />
        );
      case 2:
        return (
          <ExperienceCreationStep
            yamlContent={yamlContent}
            resolvedValues={placeholderValues}
            onCreationComplete={(createdExperience) => {
              if (onSuccess) {
                onSuccess(createdExperience);
              }
            }}
            onRetry={() => setCurrentStep(1)} // Go back to placeholder form
            onClose={handleClose} // Pass close handler to allow closing wizard
          />
        );
      default:
        return null;
    }
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: { minHeight: '600px' },
      }}
    >
      <DialogTitle>
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography variant="h6">Import Experience</Typography>
          <Button onClick={handleClose} size="small" sx={{ minWidth: 'auto', p: 1 }}>
            <CloseIcon />
          </Button>
        </Box>
      </DialogTitle>

      <DialogContent>
        {/* Stepper */}
        <Box sx={{ mb: 4 }}>
          <Stepper activeStep={currentStep} alternativeLabel>
            {STEPS.map((step, _index) => (
              <Step key={step.key}>
                <StepLabel>
                  <Typography variant="body2" fontWeight="medium">
                    {step.label}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {step.description}
                  </Typography>
                </StepLabel>
              </Step>
            ))}
          </Stepper>
        </Box>

        {/* Global error display */}
        {wizardError && (
          <Alert severity="error" sx={{ mb: 3 }}>
            {wizardError}
          </Alert>
        )}

        {/* Step content */}
        <Box sx={{ minHeight: '300px' }}>{renderStepContent()}</Box>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 3 }}>
        <Button onClick={handleClose}>Cancel</Button>

        {currentStep < STEPS.length - 1 && (
          <>
            <Button onClick={handleBack} disabled={currentStep === 0}>
              Back
            </Button>

            <Button onClick={handleNext} variant="contained" disabled={!canProceed()}>
              {getNextButtonText()}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default ImportExperienceWizard;
