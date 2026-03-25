import React, { useState, useCallback } from 'react';
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
} from '@mui/material';
import { Close as CloseIcon } from '@mui/icons-material';

import FileUploadStep from './import-kb-wizard/FileUploadStep';
import ManifestPreviewStep from './import-kb-wizard/ManifestPreviewStep';
import ImportConfirmStep from './import-kb-wizard/ImportConfirmStep';

const STEPS = [
  {
    key: 'upload',
    label: 'Upload Archive',
    description: 'Select a .zip export file',
  },
  {
    key: 'preview',
    label: 'Review Manifest',
    description: 'Verify archive contents',
  },
  {
    key: 'import',
    label: 'Import',
    description: 'Start the import',
  },
];

/**
 * ImportKBWizard - Multi-step wizard for importing knowledge bases from zip archives.
 *
 * @param {boolean} props.open - Whether the wizard dialog is open
 * @param {function} props.onClose - Callback when wizard is closed
 * @param {function} [props.onSuccess] - Callback when import is successfully queued
 */
const ImportKBWizard = ({ open, onClose, onSuccess }) => {
  const [currentStep, setCurrentStep] = useState(0);
  const [selectedFile, setSelectedFile] = useState(null);
  const [manifestData, setManifestData] = useState(null);
  const [skipEmbeddings, setSkipEmbeddings] = useState(false);

  const handleClose = useCallback(() => {
    setCurrentStep(0);
    setSelectedFile(null);
    setManifestData(null);
    setSkipEmbeddings(false);
    if (onClose) {
      onClose();
    }
  }, [onClose]);

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

  const canProceed = () => {
    switch (currentStep) {
      case 0:
        return selectedFile !== null && manifestData !== null;
      case 1:
        return manifestData?.embedding_model_match || skipEmbeddings;
      case 2:
        return false;
      default:
        return false;
    }
  };

  const renderStepContent = () => {
    switch (currentStep) {
      case 0:
        return (
          <FileUploadStep
            onFileSelected={(file) => {
              setSelectedFile(file);
              setManifestData(null);
              setSkipEmbeddings(false);
            }}
            onManifestLoaded={(manifest) => {
              setManifestData(manifest);
              if (manifest.embedding_model_match) {
                setSkipEmbeddings(false);
              }
            }}
          />
        );
      case 1:
        return (
          <ManifestPreviewStep
            manifest={manifestData}
            skipEmbeddings={skipEmbeddings}
            onSkipEmbeddingsChange={setSkipEmbeddings}
          />
        );
      case 2:
        return (
          <ImportConfirmStep
            file={selectedFile}
            skipEmbeddings={skipEmbeddings}
            onSuccess={(result) => {
              if (onSuccess) {
                onSuccess(result);
              }
            }}
            onRetry={() => setCurrentStep(1)}
          />
        );
      default:
        return null;
    }
  };

  if (!open) {
    return null;
  }

  return (
    <Dialog
      open
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: { minHeight: '500px' },
      }}
    >
      <DialogTitle>
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography variant="h6">Import Knowledge Base</Typography>
          <Button onClick={handleClose} size="small" aria-label="Close dialog" sx={{ minWidth: 'auto', p: 1 }}>
            <CloseIcon />
          </Button>
        </Box>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ mb: 4 }}>
          <Stepper activeStep={currentStep} alternativeLabel>
            {STEPS.map((step) => (
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

        <Box sx={{ minHeight: '250px' }}>{renderStepContent()}</Box>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 3 }}>
        {currentStep < STEPS.length - 1 ? (
          <>
            <Button onClick={handleClose}>Cancel</Button>
            <Button onClick={handleBack} disabled={currentStep === 0}>
              Back
            </Button>
            <Button onClick={handleNext} variant="contained" disabled={!canProceed()}>
              {currentStep === 1 ? 'Start Import' : 'Next'}
            </Button>
          </>
        ) : (
          <Button onClick={handleClose} variant="contained">
            Done
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default ImportKBWizard;
