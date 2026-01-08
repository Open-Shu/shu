import React, { useMemo, useState } from 'react';
import {
    Box,
    Button,
    Card,
    CardContent,
    Chip,
    CircularProgress,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    FormControl,
    Grid,
    IconButton,
    InputLabel,
    MenuItem,
    Select,
    Stack,
    TextField,
    Tooltip,
    Typography,
} from '@mui/material';
import {
    Add as AddIcon,
    Delete as DeleteIcon,
    Edit as EditIcon,
    Refresh as RefreshIcon,
    PlayArrow as PlayIcon,
    Schedule as ScheduleIcon,
    TouchApp as ManualIcon,
    Event as CronIcon,
    History as HistoryIcon,
    AutoAwesome as ExperiencesIcon,
    FileUpload as ImportIcon,
} from '@mui/icons-material';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { useNavigate } from 'react-router-dom';
import { experiencesAPI, extractDataFromResponse, formatError } from '../services/api';
import ExperienceRunDialog from './ExperienceRunDialog';
import ExportExperienceButton from './ExportExperienceButton';
import ImportExperienceWizard from './ImportExperienceWizard';
import PageHelpHeader from './PageHelpHeader';

// Visibility chip colors
const visibilityColors = {
    draft: 'default',
    admin_only: 'warning',
    published: 'success',
};

// Trigger type icons
const triggerIcons = {
    manual: <ManualIcon fontSize="small" />,
    scheduled: <ScheduleIcon fontSize="small" />,
    cron: <CronIcon fontSize="small" />,
};

const ExperienceCard = ({ experience, onEdit, onDelete, onRun, onHistory, isDeleting }) => {
    const visibilityLabel = experience.visibility?.replace('_', ' ') || 'draft';

    return (
        <Card
            sx={{
                transition: 'all 0.2s ease-in-out',
                '&:hover': {
                    boxShadow: 2,
                    transform: 'translateY(-1px)',
                },
            }}
        >
            <CardContent>
                <Grid container spacing={2} alignItems="center">
                    {/* Name & Description */}
                    <Grid item xs={12} sm={6} md={4}>
                        <Stack spacing={0.5}>
                            <Typography variant="h6" sx={{ fontWeight: 600 }}>
                                {experience.name}
                            </Typography>
                            {experience.description && (
                                <Typography variant="body2" color="text.secondary" noWrap>
                                    {experience.description}
                                </Typography>
                            )}
                        </Stack>
                    </Grid>

                    {/* Status chips */}
                    <Grid item xs={12} sm={6} md={4}>
                        <Stack direction="row" spacing={1} alignItems="center">
                            <Chip
                                size="small"
                                label={visibilityLabel}
                                color={visibilityColors[experience.visibility] || 'default'}
                                variant="outlined"
                            />
                            <Tooltip title={`Trigger: ${experience.trigger_type}`}>
                                <Chip
                                    size="small"
                                    icon={triggerIcons[experience.trigger_type] || triggerIcons.manual}
                                    label={experience.trigger_type}
                                    variant="outlined"
                                />
                            </Tooltip>
                            <Chip
                                size="small"
                                label={`${experience.step_count || 0} steps`}
                                variant="outlined"
                            />
                        </Stack>
                    </Grid>

                    {/* Actions */}
                    <Grid item xs={12} md={4}>
                        <Stack direction="row" spacing={1} justifyContent="flex-end">
                            <Tooltip title="Run experience">
                                <IconButton
                                    size="small"
                                    color="primary"
                                    onClick={() => onRun(experience)}
                                >
                                    <PlayIcon fontSize="small" />
                                </IconButton>
                            </Tooltip>
                            <Tooltip title="Run history">
                                <IconButton
                                    size="small"
                                    onClick={() => onHistory(experience)}
                                >
                                    <HistoryIcon fontSize="small" />
                                </IconButton>
                            </Tooltip>
                            <ExportExperienceButton
                                experienceId={experience.id}
                                experienceName={experience.name}
                                variant="icon"
                                size="small"
                            />
                            <Tooltip title="Edit experience">
                                <IconButton
                                    size="small"
                                    color="primary"
                                    onClick={() => onEdit(experience)}
                                >
                                    <EditIcon fontSize="small" />
                                </IconButton>
                            </Tooltip>
                            <Tooltip title="Delete experience">
                                <IconButton
                                    size="small"
                                    color="error"
                                    onClick={() => onDelete(experience)}
                                    disabled={isDeleting}
                                >
                                    <DeleteIcon fontSize="small" />
                                </IconButton>
                            </Tooltip>
                        </Stack>
                    </Grid>
                </Grid>

                {/* Last run info */}
                {experience.last_run_at && (
                    <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                        Last run: {new Date(experience.last_run_at).toLocaleString()}
                    </Typography>
                )}
            </CardContent>
        </Card>
    );
};

const DeleteConfirmDialog = ({ open, experience, onClose, onConfirm, isDeleting }) => (
    <Dialog open={open} onClose={onClose}>
        <DialogTitle>Delete Experience</DialogTitle>
        <DialogContent>
            <Typography>
                Are you sure you want to delete <strong>{experience?.name}</strong>?
                This will also delete all associated runs.
            </Typography>
        </DialogContent>
        <DialogActions>
            <Button onClick={onClose}>Cancel</Button>
            <Button
                onClick={onConfirm}
                color="error"
                variant="contained"
                disabled={isDeleting}
            >
                {isDeleting ? 'Deleting...' : 'Delete'}
            </Button>
        </DialogActions>
    </Dialog>
);

export default function ExperiencesAdmin() {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [deleteTarget, setDeleteTarget] = useState(null);
    const [runDialogExperience, setRunDialogExperience] = useState(null);
    const [importWizardOpen, setImportWizardOpen] = useState(false);

    // Fetch experiences
    const { data, isLoading, isFetching, error, refetch } = useQuery(
        ['experiences', 'list'],
        () => experiencesAPI.list().then(extractDataFromResponse),
        { staleTime: 5000 }
    );

    const experiences = useMemo(() => {
        const items = data?.items || [];
        // Sort by name
        return items.sort((a, b) => a.name.localeCompare(b.name));
    }, [data]);

    // Delete mutation
    const deleteMutation = useMutation(
        (id) => experiencesAPI.delete(id),
        {
            onSuccess: () => {
                queryClient.invalidateQueries(['experiences', 'list']);
                setDeleteTarget(null);
            },
        }
    );

    const handleEdit = (experience) => {
        navigate(`/admin/experiences/${experience.id}/edit`);
    };

    const handleCreate = () => {
        navigate('/admin/experiences/new');
    };

    const handleImport = () => {
        setImportWizardOpen(true);
    };

    const handleImportSuccess = (createdExperience) => {
        // Refresh the experiences list
        queryClient.invalidateQueries(['experiences', 'list']);
        // Close the import wizard
        setImportWizardOpen(false);
        // Optionally navigate to the created experience
        navigate(`/admin/experiences/${createdExperience.id}/edit`);
    };

    const handleImportClose = () => {
        setImportWizardOpen(false);
    };

    const handleDelete = (experience) => {
        setDeleteTarget(experience);
    };

    const confirmDelete = () => {
        if (deleteTarget) {
            deleteMutation.mutate(deleteTarget.id);
        }
    };

    const handleRun = (experience) => {
        setRunDialogExperience(experience);
    };

    const handleHistory = (experience) => {
        navigate(`/admin/experiences/${experience.id}/edit?tab=1`);
    };

    return (
        <Box p={3}>
            <PageHelpHeader
                title="Experiences"
                description="Create automated workflows that combine plugins, knowledge bases, and AI synthesis. Build signature experiences like Morning Briefing that can be scheduled or run manually to streamline your daily tasks."
                icon={<ExperiencesIcon />}
                tips={[
                    'Start by creating a simple experience with one or two steps to understand the workflow',
                    'Use plugins to gather data (emails, calendar events, documents) and knowledge bases for context',
                    'Draft experiences are saved automatically - publish them when ready for others to use',
                    'Scheduled experiences can run automatically using cron expressions or simple intervals',
                    'Experience runs are logged with full input/output history for debugging and review',
                ]}
            />

            {/* Header */}
            <Stack direction="row" alignItems="center" justifyContent="space-between" mb={3}>
                <Box>
                    <Typography variant="h4" sx={{ fontWeight: 600, mb: 0.5 }}>
                        Experiences
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                        {experiences.length} experience{experiences.length !== 1 ? 's' : ''}
                    </Typography>
                </Box>
                <Stack direction="row" spacing={1}>
                    <Button
                        variant="contained"
                        startIcon={<AddIcon />}
                        onClick={handleCreate}
                    >
                        New Experience
                    </Button>
                    <Button
                        variant="outlined"
                        startIcon={<ImportIcon />}
                        onClick={handleImport}
                        sx={{
                            borderColor: 'primary.main',
                            color: 'primary.main',
                            '&:hover': {
                                borderColor: 'primary.dark',
                                backgroundColor: 'primary.50',
                            },
                        }}
                    >
                        Import Experience
                    </Button>
                    <Tooltip title="Refresh list">
                        <Button
                            variant="outlined"
                            startIcon={<RefreshIcon />}
                            onClick={() => refetch()}
                            disabled={isFetching}
                        >
                            Refresh
                        </Button>
                    </Tooltip>
                </Stack>
            </Stack>

            {/* Loading State */}
            {isLoading && (
                <Box display="flex" alignItems="center" justifyContent="center" py={8}>
                    <Stack alignItems="center" spacing={2}>
                        <CircularProgress size={40} />
                        <Typography variant="body2" color="text.secondary">
                            Loading experiences...
                        </Typography>
                    </Stack>
                </Box>
            )}

            {/* Error State */}
            {error && (
                <Box
                    sx={{
                        bgcolor: 'error.50',
                        border: '1px solid',
                        borderColor: 'error.200',
                        borderRadius: 1,
                        p: 2,
                        mb: 3,
                    }}
                >
                    <Typography color="error.main">{formatError(error)}</Typography>
                </Box>
            )}

            {/* Experiences List */}
            {!isLoading && !error && (
                <Stack spacing={2}>
                    {experiences.length === 0 ? (
                        <Box
                            sx={{
                                textAlign: 'center',
                                py: 8,
                                bgcolor: 'grey.50',
                                borderRadius: 2,
                                border: '1px dashed',
                                borderColor: 'grey.300',
                            }}
                        >
                            <Typography variant="h6" color="text.secondary" gutterBottom>
                                No experiences found
                            </Typography>
                            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                                Create your first experience to get started
                            </Typography>
                            <Box
                                sx={{
                                flex: 1,
                                display: 'flex',
                                flexDirection: 'column',
                                alignItems: 'center',
                                justifyContent: 'center',
                                textAlign: 'center',
                                }}
                            >
                                <Stack direction="row" spacing={1}>
                                    <Button
                                        variant="contained"
                                        startIcon={<AddIcon />}
                                        onClick={handleCreate}
                                    >
                                        New Experience
                                    </Button>
                                    <Button
                                        variant="outlined"
                                        startIcon={<ImportIcon />}
                                        onClick={handleImport}
                                        sx={{
                                            borderColor: 'primary.main',
                                            color: 'primary.main',
                                            '&:hover': {
                                                borderColor: 'primary.dark',
                                                backgroundColor: 'primary.50',
                                            },
                                        }}
                                    >
                                        Import Experience
                                    </Button>
                                </Stack>
                            </Box>
                        </Box>
                    ) : (
                        experiences.map((exp) => (
                            <ExperienceCard
                                key={exp.id}
                                experience={exp}
                                onEdit={handleEdit}
                                onDelete={handleDelete}
                                onRun={handleRun}
                                onHistory={handleHistory}
                                isDeleting={deleteMutation.isLoading && deleteTarget?.id === exp.id}
                            />
                        ))
                    )}
                </Stack>
            )}



            {/* Delete Confirmation Dialog */}
            <DeleteConfirmDialog
                open={!!deleteTarget}
                experience={deleteTarget}
                onClose={() => setDeleteTarget(null)}
                onConfirm={confirmDelete}
                isDeleting={deleteMutation.isLoading}
            />

            {/* Run Dialog */}
            {runDialogExperience && (
                <ExperienceRunDialog
                    open={!!runDialogExperience}
                    onClose={() => setRunDialogExperience(null)}
                    experienceId={runDialogExperience.id}
                    experienceName={runDialogExperience.name}
                    steps={runDialogExperience.steps || []}
                />
            )}

            {/* Import Experience Wizard */}
            <ImportExperienceWizard
                open={importWizardOpen}
                onClose={handleImportClose}
                onSuccess={handleImportSuccess}
                onError={(error) => {
                    console.error('Import failed:', error);
                    // Keep wizard open to show error - the wizard handles error display
                }}
            />
        </Box>
    );
}
