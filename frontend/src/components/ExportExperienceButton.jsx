import React, { useState } from "react";
import { Button, CircularProgress, IconButton, Tooltip } from "@mui/material";
import { Download as DownloadIcon } from "@mui/icons-material";
import { useMutation } from "react-query";
import { experiencesAPI, formatError } from "../services/api";
import {
  downloadResponseAsFile,
  generateSafeFilename,
} from "../utils/downloadHelpers";
import { log } from "../utils/log";

/**
 * ExportExperienceButton component for exporting experiences as YAML files.
 *
 * @param {Object} props - Component props
 * @param {string} props.experienceId - ID of the experience to export
 * @param {string} props.experienceName - Name of the experience (for filename)
 * @param {string} [props.variant='icon'] - Button variant: 'icon', 'button', or 'contained'
 * @param {string} [props.size='small'] - Button size
 * @param {boolean} [props.disabled=false] - Whether the button is disabled
 * @param {function} [props.onSuccess] - Callback function called on successful export
 * @param {function} [props.onError] - Callback function called on export error
 */
const ExportExperienceButton = ({
  experienceId,
  experienceName,
  variant = "icon",
  size = "small",
  disabled = false,
  onSuccess,
  onError,
}) => {
  const [error, setError] = useState(null);

  // Export mutation
  const exportMutation = useMutation(
    () => experiencesAPI.export(experienceId),
    {
      onSuccess: (response) => {
        try {
          // Generate safe filename
          const safeName = generateSafeFilename(experienceName, "experience");
          const filename = `${safeName}-experience.yaml`;

          // Download the file
          downloadResponseAsFile(response, filename, "application/x-yaml");

          // Clear any previous errors
          setError(null);

          log.info("Experience exported successfully", {
            experienceId,
            experienceName,
            filename,
          });

          // Call success callback if provided
          if (onSuccess) {
            onSuccess();
          }
        } catch (downloadError) {
          log.error("Failed to trigger download", downloadError);
          const errorMessage = "Failed to download the exported file";
          setError(errorMessage);
          if (onError) {
            onError(errorMessage);
          }
        }
      },
      onError: (error) => {
        const errorMessage = formatError(error);
        log.error("Failed to export experience", {
          error: errorMessage,
          experienceId,
        });
        setError(errorMessage);
        if (onError) {
          onError(errorMessage);
        }
      },
    },
  );

  const handleExport = () => {
    if (!experienceId) {
      const errorMessage = "Experience ID is required for export";
      setError(errorMessage);
      if (onError) {
        onError(errorMessage);
      }
      return;
    }

    exportMutation.mutate();
  };

  // Icon button variant
  if (variant === "icon") {
    return (
      <Tooltip title={error || "Export experience as YAML"}>
        <span>
          <IconButton
            size={size}
            onClick={handleExport}
            disabled={disabled || exportMutation.isLoading}
            color={error ? "error" : "default"}
          >
            {exportMutation.isLoading ? (
              <CircularProgress size={16} />
            ) : (
              <DownloadIcon fontSize="small" />
            )}
          </IconButton>
        </span>
      </Tooltip>
    );
  }

  // Button variants
  const buttonProps = {
    size,
    onClick: handleExport,
    disabled: disabled || exportMutation.isLoading,
    startIcon: exportMutation.isLoading ? (
      <CircularProgress size={16} />
    ) : (
      <DownloadIcon />
    ),
    color: error ? "error" : "primary",
  };

  if (variant === "contained") {
    buttonProps.variant = "contained";
  } else {
    buttonProps.variant = "outlined";
  }

  return (
    <Tooltip title={error || "Export experience as YAML"}>
      <span>
        <Button {...buttonProps}>
          {exportMutation.isLoading ? "Exporting..." : "Export"}
        </Button>
      </span>
    </Tooltip>
  );
};

export default ExportExperienceButton;
