import React from 'react';
import {
  Grid,
  TextField,
  FormControlLabel,
  Switch,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  InputAdornment,
  FormHelperText,
} from '@mui/material';
import HelpTooltip from './HelpTooltip.jsx';

// Utility to build defaults for a given schema definition
export function buildDefaultForDef(def) {
  if (!def) {
    return null;
  }
  const tList = Array.isArray(def.type) ? def.type : [def.type];
  const t = tList[0];
  if (def.default !== undefined) {
    return def.default;
  }
  if (t === 'string') {
    return '';
  }
  if (t === 'number' || t === 'integer') {
    return 0;
  }
  if (t === 'boolean') {
    return false;
  }
  if (t === 'array') {
    return [];
  }
  if (t === 'object') {
    const props = def.properties || {};
    const obj = {};
    for (const [k, sub] of Object.entries(props)) {
      obj[k] = buildDefaultForDef(sub);
    }
    return obj;
  }
  return null;
}

function humanizeEnum(val) {
  if (val === null || val === undefined) {
    return '';
  }
  const s = String(val);
  if (!s) {
    return '';
  }
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function buildDefaultValues(schema) {
  if (!schema || typeof schema !== 'object') {
    return {};
  }
  const props = schema.properties || {};
  const out = {};
  for (const [key, def] of Object.entries(props)) {
    out[key] = buildDefaultForDef(def);
  }
  return out;
}

/**
 * SchemaForm: unified renderer for plugin tool input schema
 * - Honors x-ui.hidden and x_ui.hidden
 * - Hides any field with x-binding present (host-bound)
 * - Allows caller to hide specific keys
 */
export default function SchemaForm({ schema, values, onChangeField, hideKeys = new Set() }) {
  if (!schema || !schema.properties) {
    return null;
  }

  const entries = Object.entries(schema.properties);

  // Helper: evaluate x-ui.show_when visibility rules against current values
  const isVisible = (xui) => {
    const sw = xui && xui.show_when;
    if (!sw) {
      return true;
    }
    try {
      // Support forms:
      // 1) { field: 'auth_mode', equals: 'domain_delegate' }
      // 2) { field: 'auth_mode', in: ['domain_delegate','x'] }
      // 3) { auth_mode: 'domain_delegate' } or { auth_mode: ['domain_delegate','x'] }
      if (typeof sw === 'object' && 'field' in sw) {
        const val = values?.[sw.field];
        if (Array.isArray(sw.in)) {
          return sw.in.includes(val);
        }
        if (sw.equals !== undefined) {
          return val === sw.equals;
        }
        if (sw.notEquals !== undefined) {
          return val !== sw.notEquals;
        }
        return true;
      }
      if (typeof sw === 'object') {
        return Object.entries(sw).every(([k, v]) => {
          const val = values?.[k];
          if (Array.isArray(v)) {
            return v.includes(val);
          }
          return val === v;
        });
      }
      return true;
    } catch (_e) {
      return true;
    }
  };

  return (
    <Grid container spacing={2}>
      {entries.map(([key, def]) => {
        const type = Array.isArray(def?.type) ? def.type[0] : def?.type;
        const hasEnum = Array.isArray(def?.enum) && def.enum.length > 0;
        const xui = (def && (def['x-ui'] || def['x_ui'])) || {};
        const hidden = (xui && xui.hidden === true) || Boolean(def && def['x-binding']);
        if (!isVisible(xui) || hidden || hideKeys.has(key)) {
          return null;
        }

        if (type === 'boolean') {
          return (
            <Grid item xs={12} key={key}>
              <FormControlLabel
                control={
                  <Switch checked={!!values[key]} onChange={(e) => onChangeField(key, 'boolean', e.target.checked)} />
                }
                label={
                  <>
                    {key}
                    {xui?.help && <HelpTooltip title={xui.help} placement="top" />}
                  </>
                }
              />
            </Grid>
          );
        }

        if (type === 'number' || type === 'integer') {
          return (
            <Grid item xs={12} key={key}>
              <TextField
                fullWidth
                type="number"
                label={key}
                value={values[key] ?? 0}
                onChange={(e) => onChangeField(key, type, e.target.value)}
                InputProps={
                  xui?.help
                    ? {
                        endAdornment: (
                          <InputAdornment position="end">
                            <HelpTooltip title={xui.help} placement="top" />
                          </InputAdornment>
                        ),
                      }
                    : undefined
                }
              />
            </Grid>
          );
        }

        if (type === 'string' && hasEnum) {
          const nullable = Array.isArray(def?.type) && def.type.includes('null');
          const enumLabels = (xui && xui.enum_labels) || {};
          const enumHelp = (xui && xui.enum_help) || {};
          const placeholder = (xui && xui.placeholder) || (nullable ? 'Auto' : undefined);
          const current = values[key] === null || values[key] === undefined ? '' : values[key];
          const options = (def.enum || []).filter((opt) => opt !== null && String(opt).toLowerCase() !== 'null');
          return (
            <Grid item xs={12} key={key}>
              <FormControl fullWidth>
                <InputLabel id={`${key}-label`}>{key}</InputLabel>
                <Select
                  labelId={`${key}-label`}
                  label={key}
                  value={current}
                  onChange={(e) => {
                    const v = e.target.value;
                    onChangeField(key, 'string', v === '' ? null : v);
                  }}
                >
                  {placeholder !== undefined && <MenuItem value="">{placeholder}</MenuItem>}
                  {options.map((opt) => (
                    <MenuItem key={String(opt)} value={opt} title={enumHelp[String(opt)] || ''}>
                      {enumLabels[String(opt)] || humanizeEnum(opt)}
                    </MenuItem>
                  ))}
                </Select>
                {(() => {
                  const helper = enumHelp[String(current)] || xui?.help;
                  return helper ? <FormHelperText>{helper}</FormHelperText> : null;
                })()}
              </FormControl>
            </Grid>
          );
        }

        if (type === 'string') {
          return (
            <Grid item xs={12} key={key}>
              <TextField
                fullWidth
                label={key}
                value={values[key] ?? ''}
                onChange={(e) => onChangeField(key, 'string', e.target.value)}
                InputProps={
                  xui?.help
                    ? {
                        endAdornment: (
                          <InputAdornment position="end">
                            <HelpTooltip title={xui.help} placement="top" />
                          </InputAdornment>
                        ),
                      }
                    : undefined
                }
              />
            </Grid>
          );
        }

        // Object/Array or unknown: JSON field per key (prefill with defaults)
        const defaultVal = buildDefaultForDef(def);
        const displayVal = values[key] !== undefined ? values[key] : defaultVal;
        const isArray = type === 'array';
        return (
          <Grid item xs={12} key={key}>
            <TextField
              fullWidth
              multiline
              minRows={3}
              label={`${key} (${isArray ? 'JSON array' : 'JSON object'})`}
              value={JSON.stringify(displayVal, null, 2)}
              onChange={(e) => {
                try {
                  const parsed = JSON.parse(e.target.value);
                  onChangeField(key, isArray ? 'array' : 'object', parsed);
                } catch (_err) {
                  // Ignore until valid JSON
                }
              }}
              InputProps={
                xui?.help
                  ? {
                      endAdornment: (
                        <InputAdornment position="end">
                          <HelpTooltip title={xui.help} placement="top" />
                        </InputAdornment>
                      ),
                    }
                  : undefined
              }
            />
          </Grid>
        );
      })}
    </Grid>
  );
}
