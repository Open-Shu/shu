import { useMemo } from 'react';
import {
    Autocomplete,
    TextField,
    Box,
    Typography,
} from '@mui/material';

/**
 * TimezoneSelector - A reusable timezone selection component
 * 
 * @param {Object} props - Component props
 * @param {string} props.value - Current timezone value (IANA format)
 * @param {function} props.onChange - Callback when timezone changes (timezone) => void
 * @param {string} props.error - Error message to display
 * @param {string} props.helperText - Helper text to display
 * @param {string} props.placeholder - Placeholder text
 * @param {boolean} props.fullWidth - Whether to take full width
 * @param {boolean} props.required - Whether timezone selection is required
 */
const TimezoneSelector = ({
    value = '',
    onChange,
    error = '',
    helperText = 'Choose the timezone for schedule execution',
    placeholder = 'Select timezone',
    fullWidth = true,
    required = false,
}) => {
    // Timezone options grouped by region
    const timezoneOptions = useMemo(() => {
        let timezones = [];
        
        try {
            // Try to get all supported timezones from the browser
            if (typeof Intl !== 'undefined' && Intl.supportedValuesOf) {
                const allTimezones = Intl.supportedValuesOf('timeZone');
                
                timezones = allTimezones.map(tz => {
                    // Extract region from timezone (e.g., 'America/New_York' -> 'America')
                    const parts = tz.split('/');
                    const region = parts[0];
                    
                    // Format label (e.g., 'America/New_York' -> 'New York')
                    let label = parts.slice(1).join(' / ').replace(/_/g, ' ');
                    
                    // If no label (e.g., 'UTC'), use the timezone itself
                    if (!label) {
                        label = tz;
                    }
                    
                    // Map region names for better grouping
                    let displayRegion = region;
                    if (region === 'Etc') {
                        displayRegion = 'UTC';
                    } else if (region === 'UTC') {
                        displayRegion = 'UTC';
                    } else if (region === 'America') {
                        displayRegion = 'Americas';
                    } else if (region === 'Australia' || region === 'Pacific') {
                        displayRegion = 'Australia & Pacific';
                    }
                    
                    return {
                        label: label,
                        value: tz,
                        region: displayRegion
                    };
                });
                
                // Ensure UTC is always included (some browsers might not include it)
                const hasUTC = timezones.some(tz => tz.value === 'UTC');
                if (!hasUTC) {
                    timezones.push({
                        label: 'UTC (Coordinated Universal Time)',
                        value: 'UTC',
                        region: 'UTC'
                    });
                }
            } else {
                throw new Error('Intl.supportedValuesOf not available');
            }
        } catch (error) {
            // Fallback to curated list if browser doesn't support it
            timezones = [
                // Americas
                { label: 'Eastern Time (New York)', value: 'America/New_York', region: 'Americas' },
                { label: 'Central Time (Chicago)', value: 'America/Chicago', region: 'Americas' },
                { label: 'Mountain Time (Denver)', value: 'America/Denver', region: 'Americas' },
                { label: 'Pacific Time (Los Angeles)', value: 'America/Los_Angeles', region: 'Americas' },
                { label: 'Atlantic Time (Halifax)', value: 'America/Halifax', region: 'Americas' },
                { label: 'São Paulo', value: 'America/Sao_Paulo', region: 'Americas' },
                { label: 'Mexico City', value: 'America/Mexico_City', region: 'Americas' },
                { label: 'Toronto', value: 'America/Toronto', region: 'Americas' },
                { label: 'Vancouver', value: 'America/Vancouver', region: 'Americas' },
                { label: 'Buenos Aires', value: 'America/Argentina/Buenos_Aires', region: 'Americas' },
                
                // Europe
                { label: 'London (GMT/BST)', value: 'Europe/London', region: 'Europe' },
                { label: 'Paris (CET/CEST)', value: 'Europe/Paris', region: 'Europe' },
                { label: 'Berlin (CET/CEST)', value: 'Europe/Berlin', region: 'Europe' },
                { label: 'Rome (CET/CEST)', value: 'Europe/Rome', region: 'Europe' },
                { label: 'Madrid (CET/CEST)', value: 'Europe/Madrid', region: 'Europe' },
                { label: 'Amsterdam (CET/CEST)', value: 'Europe/Amsterdam', region: 'Europe' },
                { label: 'Stockholm (CET/CEST)', value: 'Europe/Stockholm', region: 'Europe' },
                { label: 'Moscow (MSK)', value: 'Europe/Moscow', region: 'Europe' },
                { label: 'Dublin', value: 'Europe/Dublin', region: 'Europe' },
                { label: 'Zurich', value: 'Europe/Zurich', region: 'Europe' },
                
                // Asia
                { label: 'Tokyo (JST)', value: 'Asia/Tokyo', region: 'Asia' },
                { label: 'Shanghai (CST)', value: 'Asia/Shanghai', region: 'Asia' },
                { label: 'Hong Kong (HKT)', value: 'Asia/Hong_Kong', region: 'Asia' },
                { label: 'Singapore (SGT)', value: 'Asia/Singapore', region: 'Asia' },
                { label: 'Seoul (KST)', value: 'Asia/Seoul', region: 'Asia' },
                { label: 'Mumbai (IST)', value: 'Asia/Kolkata', region: 'Asia' },
                { label: 'Dubai (GST)', value: 'Asia/Dubai', region: 'Asia' },
                { label: 'Bangkok', value: 'Asia/Bangkok', region: 'Asia' },
                { label: 'Jakarta', value: 'Asia/Jakarta', region: 'Asia' },
                
                // Australia & Pacific
                { label: 'Sydney (AEST/AEDT)', value: 'Australia/Sydney', region: 'Australia & Pacific' },
                { label: 'Melbourne (AEST/AEDT)', value: 'Australia/Melbourne', region: 'Australia & Pacific' },
                { label: 'Perth (AWST)', value: 'Australia/Perth', region: 'Australia & Pacific' },
                { label: 'Auckland (NZST/NZDT)', value: 'Pacific/Auckland', region: 'Australia & Pacific' },
                { label: 'Brisbane', value: 'Australia/Brisbane', region: 'Australia & Pacific' },
                
                // Africa
                { label: 'Cairo', value: 'Africa/Cairo', region: 'Africa' },
                { label: 'Johannesburg', value: 'Africa/Johannesburg', region: 'Africa' },
                { label: 'Lagos', value: 'Africa/Lagos', region: 'Africa' },
                
                // UTC
                { label: 'UTC (Coordinated Universal Time)', value: 'UTC', region: 'UTC' },
            ];
        }

        return timezones.sort((a, b) => {
            if (a.region !== b.region) {
                return a.region.localeCompare(b.region);
            }
            return a.label.localeCompare(b.label);
        });
    }, []);

    const handleTimezoneChange = (_, newValue) => {
        const selectedTimezone = newValue ? newValue.value : '';
        onChange(selectedTimezone);
    };

    const selectedTimezone = timezoneOptions.find(tz => tz.value === value);

    return (
        <Autocomplete
            value={selectedTimezone || null}
            onChange={handleTimezoneChange}
            options={timezoneOptions}
            groupBy={(option) => option.region}
            getOptionLabel={(option) => option.label}
            isOptionEqualToValue={(option, value) => option.value === value.value}
            fullWidth={fullWidth}
            renderInput={(params) => (
                <TextField
                    {...params}
                    label={`Timezone${required ? ' *' : ''}`}
                    placeholder={placeholder}
                    error={!!error}
                    helperText={error || helperText}
                />
            )}
            renderOption={(props, option) => (
                <li {...props}>
                    <Box sx={{ width: '100%' }}>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>
                            {option.label}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                            {option.value}
                        </Typography>
                    </Box>
                </li>
            )}
        />
    );
};

export default TimezoneSelector;
