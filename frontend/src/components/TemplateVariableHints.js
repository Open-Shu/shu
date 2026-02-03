import React from "react";
import { Box, Chip, Typography } from "@mui/material";
import {
  Person as UserIcon,
  Code as CodeIcon,
  History as HistoryIcon,
  AccessTime as TimeIcon,
} from "@mui/icons-material";

/**
 * Clickable variable hints for Jinja2 template fields.
 *
 * Props:
 * - steps: Array of step objects with step_key property
 * - includePreviousRun: Boolean to show previous_run variables
 * - onInsert: Callback(variableText) when chip is clicked
 */
export default function TemplateVariableHints({
  steps = [],
  includePreviousRun = false,
  onInsert,
}) {
  const handleInsert = (variable) => {
    if (onInsert) {
      onInsert(`{{ ${variable} }}`);
    }
  };

  // Group variables by category
  const userVariables = [
    { label: "user.id", variable: "user.id" },
    { label: "user.email", variable: "user.email" },
    { label: "user.display_name", variable: "user.display_name" },
  ];

  const stepVariables = steps
    .filter((s) => s.step_key)
    .map((s) => ({
      label: `step_outputs.${s.step_key}`,
      variable: `step_outputs.${s.step_key}`,
    }));

  const contextVariables = [{ label: "now", variable: "now" }];

  if (includePreviousRun) {
    contextVariables.push(
      {
        label: "previous_run.result_content",
        variable: "previous_run.result_content",
      },
      {
        label: "previous_run.step_outputs",
        variable: "previous_run.step_outputs",
      },
    );
  }

  return (
    <Box sx={{ mt: 1 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ mb: 0.5, display: "block" }}
      >
        Click to insert variable:
      </Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
        {/* User variables */}
        {userVariables.map((v) => (
          <Chip
            key={v.variable}
            label={v.label}
            size="small"
            icon={<UserIcon sx={{ fontSize: 14 }} />}
            onClick={() => handleInsert(v.variable)}
            sx={{
              cursor: "pointer",
              fontSize: "0.7rem",
              height: 24,
              "& .MuiChip-icon": { fontSize: 14 },
              "&:hover": { bgcolor: "action.hover" },
            }}
            variant="outlined"
          />
        ))}

        {/* Step variables */}
        {stepVariables.map((v) => (
          <Chip
            key={v.variable}
            label={v.label}
            size="small"
            icon={<CodeIcon sx={{ fontSize: 14 }} />}
            onClick={() => handleInsert(v.variable)}
            sx={{
              cursor: "pointer",
              fontSize: "0.7rem",
              height: 24,
              "& .MuiChip-icon": { fontSize: 14 },
              "&:hover": { bgcolor: "action.hover" },
            }}
            variant="outlined"
            color="primary"
          />
        ))}

        {/* Context variables */}
        {contextVariables.map((v) => (
          <Chip
            key={v.variable}
            label={v.label}
            size="small"
            icon={
              v.variable.startsWith("previous") ? (
                <HistoryIcon sx={{ fontSize: 14 }} />
              ) : (
                <TimeIcon sx={{ fontSize: 14 }} />
              )
            }
            onClick={() => handleInsert(v.variable)}
            sx={{
              cursor: "pointer",
              fontSize: "0.7rem",
              height: 24,
              "& .MuiChip-icon": { fontSize: 14 },
              "&:hover": { bgcolor: "action.hover" },
            }}
            variant="outlined"
            color="secondary"
          />
        ))}
      </Box>
    </Box>
  );
}
