import React from "react";
import {
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  FormHelperText,
} from "@mui/material";

/**
 * StyledSelect - A properly configured Select component that prevents common styling issues
 *
 * This component enforces the correct pattern for Material-UI Select components:
 * - Uses only InputLabel for labeling (no conflicting label prop)
 * - No displayEmpty or custom renderValue that interfere with label behavior
 * - Consistent styling that works with the global theme
 *
 * Usage:
 * <StyledSelect
 *   label="Select an option"
 *   value={selectedValue}
 *   onChange={(value) => setSelectedValue(value)}
 *   options={[
 *     { value: 'option1', label: 'Option 1' },
 *     { value: 'option2', label: 'Option 2' }
 *   ]}
 *   disabled={false}
 *   allowEmpty={true}
 *   emptyLabel="None"
 *   fullWidth={true}
 *   margin="normal"
 * />
 */
function StyledSelect({
  label,
  value,
  onChange,
  options = [],
  disabled = false,
  allowEmpty = false,
  emptyLabel = "None",
  emptyValue = "",
  fullWidth = true,
  margin = "normal",
  variant = "outlined",
  required = false,
  helperText,
  error = false,
  size = "medium",
  ...otherProps
}) {
  // Generate unique IDs for accessibility
  const labelId = `styled-select-label-${Math.random().toString(36).substr(2, 9)}`;

  const handleChange = (event) => {
    if (onChange) {
      onChange(event.target.value, event);
    }
  };

  return (
    <FormControl
      fullWidth={fullWidth}
      margin={margin}
      variant={variant}
      required={required}
      error={error}
      size={size}
      {...otherProps}
    >
      <InputLabel id={labelId}>{label}</InputLabel>
      <Select
        labelId={labelId}
        value={value}
        onChange={handleChange}
        disabled={disabled}
      >
        {allowEmpty && (
          <MenuItem value={emptyValue}>
            <span style={{ color: "#999", fontStyle: "italic" }}>
              {emptyLabel}
            </span>
          </MenuItem>
        )}
        {options.map((option, index) => {
          // Handle both object and primitive options
          const optionValue =
            typeof option === "object" ? option.value : option;
          const optionLabel =
            typeof option === "object" ? option.label : option;
          const optionKey =
            typeof option === "object" && option.key
              ? option.key
              : `option-${index}`;

          return (
            <MenuItem key={optionKey} value={optionValue}>
              {optionLabel}
            </MenuItem>
          );
        })}
      </Select>
      {helperText && <FormHelperText>{helperText}</FormHelperText>}
    </FormControl>
  );
}

export default StyledSelect;
