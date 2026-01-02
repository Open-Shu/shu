import React from 'react';
import { CircularProgress } from '@mui/material';
import {
    CheckCircle as CheckIcon,
    Error as ErrorIcon,
    RadioButtonUnchecked as PendingIcon,
    SkipNext as SkipIcon,
} from '@mui/icons-material';

/**
 * Renders an icon representing the status of an experience step.
 * 
 * @param {Object} props
 * @param {Object} [props.state] - Step state object with a `status` field
 * @param {string} [props.state.status] - One of: 'running', 'succeeded', 'failed', 'skipped', or undefined
 * @returns {JSX.Element} Status icon component
 */
export default function StepStatusIcon({ state }) {
    if (!state) {
        return <PendingIcon color="disabled" />;
    }

    switch (state.status) {
        case 'running':
            return <CircularProgress size={20} />;
        case 'succeeded':
            return <CheckIcon color="success" />;
        case 'failed':
            return <ErrorIcon color="error" />;
        case 'skipped':
            return <SkipIcon color="action" />;
        default:
            return <PendingIcon color="disabled" />;
    }
}
