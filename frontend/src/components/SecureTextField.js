import React, { useState } from 'react';
import { TextField, InputAdornment, IconButton, Tooltip } from '@mui/material';
import { Visibility, VisibilityOff } from '@mui/icons-material';

/**
 * SecureTextField - A text field component for handling sensitive data like API keys
 *
 * Features:
 * - Always shows the field (not blank unless actually blank in database)
 * - Obscured by default (password dots)
 * - Show/hide toggle to make it visible and editable
 * - No edit button - just the show/hide toggle
 */
const SecureTextField = ({
  label,
  value,
  onChange,
  hasExistingValue = false,
  placeholder = 'Leave empty to keep existing value',
  editPlaceholder = 'Enter new value',
  fullWidth = true,
  margin = 'normal',
  disabled = false,
  ...textFieldProps
}) => {
  const [showValue, setShowValue] = useState(false);

  const handleToggleVisibility = () => {
    setShowValue(!showValue);
  };

  const handleChange = (e) => {
    onChange(e);
  };

  // Determine what value to show and field behavior
  const shouldShowMasked = hasExistingValue && !value && !showValue;
  const fieldValue = shouldShowMasked ? '••••••••••••••••••••••••••••••••' : value || '';

  // Always show a single field with show/hide toggle
  return (
    <TextField
      fullWidth={fullWidth}
      margin={margin}
      label={label}
      type={showValue ? 'text' : 'password'}
      value={fieldValue}
      onChange={handleChange}
      disabled={disabled}
      placeholder={hasExistingValue ? placeholder : editPlaceholder}
      InputProps={{
        readOnly: shouldShowMasked,
        endAdornment: (
          <InputAdornment position="end">
            <Tooltip title={showValue ? 'Hide' : 'Show'}>
              <IconButton onClick={handleToggleVisibility} edge="end" disabled={disabled}>
                {showValue ? <VisibilityOff /> : <Visibility />}
              </IconButton>
            </Tooltip>
          </InputAdornment>
        ),
      }}
      helperText={hasExistingValue && !value ? 'Leave empty to keep existing API key' : 'Enter your API key'}
      {...textFieldProps}
    />
  );
};

export default SecureTextField;
