import {
  Box,
  Chip,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';

/**
 * DataRenderer - A reusable component for rendering structured data in a human-readable format.
 *
 * Handles various data types:
 * - Primitives (null, boolean, number, string)
 * - Arrays (as chips for primitives, tables for objects, nested lists for mixed)
 * - Objects (as key-value pairs with nested rendering)
 *
 * @param {Object} props
 * @param {*} props.data - The data to render
 * @param {number} props.depth - Current nesting depth (used internally for recursion)
 */
export default function DataRenderer({ data, depth = 0 }) {
  // Helper function to render primitive values
  const renderValue = (value) => {
    if (value === null || value === undefined) {
      return (
        <Typography variant="body2" color="text.secondary" fontStyle="italic">
          null
        </Typography>
      );
    }

    if (typeof value === 'boolean') {
      return <Chip label={value ? 'true' : 'false'} size="small" color={value ? 'success' : 'default'} />;
    }

    if (typeof value === 'number') {
      return <Typography variant="body2">{value}</Typography>;
    }

    if (typeof value === 'string') {
      // Check if it's a date string
      if (value.match(/^\d{4}-\d{2}-\d{2}/) && !isNaN(Date.parse(value))) {
        return <Typography variant="body2">{new Date(value).toLocaleString()}</Typography>;
      }
      return <Typography variant="body2">{value}</Typography>;
    }

    if (Array.isArray(value)) {
      if (value.length === 0) {
        return (
          <Typography variant="body2" color="text.secondary" fontStyle="italic">
            empty array
          </Typography>
        );
      }
      // For arrays of primitives, show as chips
      if (value.every((item) => item === null || item === undefined || typeof item !== 'object')) {
        return (
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
            {value.map((item, idx) => (
              <Chip key={idx} label={String(item)} size="small" variant="outlined" />
            ))}
          </Box>
        );
      }
      // For arrays of objects, show count
      return (
        <Typography variant="body2" color="text.secondary">
          {value.length} items
        </Typography>
      );
    }

    if (typeof value === 'object') {
      return (
        <Typography variant="body2" color="text.secondary">
          object
        </Typography>
      );
    }

    return <Typography variant="body2">{String(value)}</Typography>;
  };

  // Main rendering logic
  if (!data || typeof data !== 'object') {
    return renderValue(data);
  }

  if (Array.isArray(data)) {
    if (data.length === 0) {
      return (
        <Typography variant="body2" color="text.secondary" fontStyle="italic">
          Empty array
        </Typography>
      );
    }

    // If array of objects with similar structure, render as table
    // Explicitly exclude null since typeof null === 'object'
    if (data.every((item) => item !== null && typeof item === 'object' && !Array.isArray(item))) {
      // Only extract keys from non-null objects
      const allKeys = [...new Set(data.flatMap((item) => (item !== null ? Object.keys(item) : [])))];

      return (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                {allKeys.map((key) => (
                  <TableCell key={key} sx={{ fontWeight: 600 }}>
                    {key.replace(/_/g, ' ')}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {data.map((item, idx) => (
                <TableRow key={idx}>
                  {allKeys.map((key) => (
                    <TableCell key={key}>{renderValue(item[key])}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      );
    }

    // Otherwise, render as list
    return (
      <Stack spacing={1}>
        {data.map((item, idx) => (
          <Box key={idx} sx={{ pl: 2, borderLeft: 2, borderColor: 'divider' }}>
            <DataRenderer data={item} depth={depth + 1} />
          </Box>
        ))}
      </Stack>
    );
  }

  // Render object as key-value pairs
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" fontStyle="italic">
        Empty object
      </Typography>
    );
  }

  return (
    <Stack spacing={1.5}>
      {entries.map(([key, value]) => {
        const isNested = value && typeof value === 'object';

        return (
          <Box key={key}>
            <Typography
              variant="caption"
              sx={{
                fontWeight: 600,
                color: 'text.secondary',
                textTransform: 'uppercase',
                letterSpacing: 0.5,
                display: 'block',
                mb: 0.5,
              }}
            >
              {key.replace(/_/g, ' ')}
            </Typography>
            {isNested ? (
              <Box
                sx={{
                  pl: 2,
                  borderLeft: 2,
                  borderColor: 'divider',
                  mt: 0.5,
                }}
              >
                <DataRenderer data={value} depth={depth + 1} />
              </Box>
            ) : (
              renderValue(value)
            )}
          </Box>
        );
      })}
    </Stack>
  );
}
