import React, { useState } from 'react';
import { useQuery } from 'react-query';
import {
    Box,
    Chip,
    IconButton,
    Paper,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TablePagination,
    TableRow,
    Tooltip,
    Typography,
    Stack,
    Button,
    CircularProgress,
    Alert,
} from '@mui/material';
import {
    Visibility as ViewIcon,
    Refresh as RefreshIcon,
    CheckCircle as SuccessIcon,
    Error as ErrorIcon,
    Cancel as CancelIcon,
    HourglassEmpty as PendingIcon,
} from '@mui/icons-material';
import { format } from 'date-fns';
import { experiencesAPI, extractItemsFromResponse, extractPaginationFromResponse } from '../services/api';
import ExperienceRunDetailDialog from './ExperienceRunDetailDialog';

const StatusChip = ({ status }) => {
    let color = 'default';
    let icon = <PendingIcon />;

    switch (status) {
        case 'succeeded':
            color = 'success';
            icon = <SuccessIcon />;
            break;
        case 'failed':
            color = 'error';
            icon = <ErrorIcon />;
            break;
        case 'running':
            color = 'primary';
            icon = <CircularProgress size={16} />;
            break;
        case 'cancelled':
            color = 'warning';
            icon = <CancelIcon />;
            break;
        default:
            break;
    }

    return (
        <Chip
            icon={icon}
            label={status}
            color={color}
            size="small"
            variant="outlined"
        />
    );
};

export default function ExperienceRunsList({ experienceId }) {
    const [page, setPage] = useState(0);
    const [rowsPerPage, setRowsPerPage] = useState(10);
    const [selectedRunId, setSelectedRunId] = useState(null);

    const { data, isLoading, error, refetch } = useQuery(
        ['experience-runs', experienceId, page, rowsPerPage],
        () => experiencesAPI.listRuns(experienceId, { page: page + 1, size: rowsPerPage }),
        {
            keepPreviousData: true,
            staleTime: 10000,
        }
    );

    const runs = data ? extractItemsFromResponse(data) : [];
    const pagination = data ? extractPaginationFromResponse(data) : null;
    const totalCount = pagination?.total || 0;

    const handleChangePage = (event, newPage) => {
        setPage(newPage);
    };

    const handleChangeRowsPerPage = (event) => {
        setRowsPerPage(parseInt(event.target.value, 10));
        setPage(0);
    };

    if (isLoading && !data) {
        return (
            <Box display="flex" justifyContent="center" p={4}>
                <CircularProgress />
            </Box>
        );
    }

    if (error) {
        return (
            <Alert severity="error">
                Failed to load runs: {error.message}
            </Alert>
        );
    }

    return (
        <Box>
            <Stack direction="row" justifyContent="flex-end" mb={2}>
                <Button
                    startIcon={<RefreshIcon />}
                    onClick={() => refetch()}
                    size="small"
                >
                    Refresh
                </Button>
            </Stack>

            <TableContainer component={Paper} variant="outlined">
                <Table>
                    <TableHead>
                        <TableRow>
                            <TableCell>Status</TableCell>
                            <TableCell>Started At</TableCell>
                            <TableCell>Duration</TableCell>
                            <TableCell>User</TableCell>
                            <TableCell>Model</TableCell>
                            <TableCell align="right">Actions</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {runs.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={6} align="center">
                                    <Typography color="text.secondary" py={4}>
                                        No runs found.
                                    </Typography>
                                </TableCell>
                            </TableRow>
                        ) : (
                            runs.map((run) => {
                                const start = new Date(run.started_at);
                                const end = run.finished_at ? new Date(run.finished_at) : null;
                                const duration = end ? ((end - start) / 1000).toFixed(1) + 's' : '-';

                                return (
                                    <TableRow key={run.id} hover>
                                        <TableCell>
                                            <StatusChip status={run.status} />
                                        </TableCell>
                                        <TableCell>
                                            {format(start, 'MMM d, HH:mm:ss')}
                                        </TableCell>
                                        <TableCell>{duration}</TableCell>
                                        <TableCell>
                                            {run.user?.email || run.user_id}
                                        </TableCell>
                                        <TableCell>
                                            {run.result_metadata?.model_configuration ? (
                                                <Box>
                                                    <Typography variant="body2">
                                                        {run.result_metadata.model_configuration.name}
                                                    </Typography>
                                                    <Typography variant="caption" color="textSecondary">
                                                        {run.result_metadata.model_configuration.provider_name} - {run.result_metadata.model_configuration.model_name}
                                                    </Typography>
                                                </Box>
                                            ) : run.model_name ? (
                                                <Typography variant="body2" color="textSecondary">
                                                    {run.model_name} (Legacy)
                                                </Typography>
                                            ) : (
                                                <Typography variant="body2" color="textSecondary">
                                                    No LLM used
                                                </Typography>
                                            )}
                                        </TableCell>
                                        <TableCell align="right">
                                            <Tooltip title="View Details">
                                                <IconButton
                                                    size="small"
                                                    onClick={() => setSelectedRunId(run.id)}
                                                >
                                                    <ViewIcon />
                                                </IconButton>
                                            </Tooltip>
                                        </TableCell>
                                    </TableRow>
                                );
                            })
                        )}
                    </TableBody>
                </Table>
                <TablePagination
                    rowsPerPageOptions={[5, 10, 25]}
                    component="div"
                    count={totalCount}
                    rowsPerPage={rowsPerPage}
                    page={page}
                    onPageChange={handleChangePage}
                    onRowsPerPageChange={handleChangeRowsPerPage}
                />
            </TableContainer>

            {selectedRunId && (
                <ExperienceRunDetailDialog
                    open={!!selectedRunId}
                    onClose={() => setSelectedRunId(null)}
                    runId={selectedRunId}
                />
            )}
        </Box>
    );
}
