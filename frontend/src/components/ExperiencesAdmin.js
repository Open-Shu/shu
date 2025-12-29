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
} from '@mui/icons-material';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { experiencesAPI, extractDataFromResponse, formatError } from '../services/api';

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

const ExperienceCard = ({ experience, onEdit, onDelete, isDeleting }) => {
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
                            <Tooltip title="Run experience (not implemented)">
                                <span>
                                    <IconButton size="small" disabled>
                                        <PlayIcon fontSize="small" />
                                    </IconButton>
                                </span>
                            </Tooltip>
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

const CreateExperienceDialog = ({ open, onClose, onCreate, isCreating }) => {
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [visibility, setVisibility] = useState('draft');
    const [triggerType, setTriggerType] = useState('manual');

    const handleSubmit = () => {
        onCreate({
            name,
            description: description || null,
            visibility,
            trigger_type: triggerType,
            steps: [],
        });
    };

    const handleClose = () => {
        setName('');
        setDescription('');
        setVisibility('draft');
        setTriggerType('manual');
        onClose();
    };

    return (
        <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
            <DialogTitle>Create New Experience</DialogTitle>
            <DialogContent>
                <Stack spacing={2} sx={{ mt: 1 }}>
                    <TextField
                        label="Name"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        fullWidth
                        required
                        autoFocus
                    />
                    <TextField
                        label="Description"
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        fullWidth
                        multiline
                        rows={2}
                    />
                    <FormControl fullWidth>
                        <InputLabel>Visibility</InputLabel>
                        <Select
                            value={visibility}
                            label="Visibility"
                            onChange={(e) => setVisibility(e.target.value)}
                        >
                            <MenuItem value="draft">Draft</MenuItem>
                            <MenuItem value="admin_only">Admin Only</MenuItem>
                            <MenuItem value="published">Published</MenuItem>
                        </Select>
                    </FormControl>
                    <FormControl fullWidth>
                        <InputLabel>Trigger Type</InputLabel>
                        <Select
                            value={triggerType}
                            label="Trigger Type"
                            onChange={(e) => setTriggerType(e.target.value)}
                        >
                            <MenuItem value="manual">Manual</MenuItem>
                            <MenuItem value="scheduled">Scheduled</MenuItem>
                            <MenuItem value="cron">Cron</MenuItem>
                        </Select>
                    </FormControl>
                </Stack>
            </DialogContent>
            <DialogActions>
                <Button onClick={handleClose}>Cancel</Button>
                <Button
                    onClick={handleSubmit}
                    variant="contained"
                    disabled={!name.trim() || isCreating}
                >
                    {isCreating ? 'Creating...' : 'Create'}
                </Button>
            </DialogActions>
        </Dialog>
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
    const [createDialogOpen, setCreateDialogOpen] = useState(false);
    const [deleteTarget, setDeleteTarget] = useState(null);

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

    // Create mutation
    const createMutation = useMutation(
        (newExp) => experiencesAPI.create(newExp).then(extractDataFromResponse),
        {
            onSuccess: () => {
                queryClient.invalidateQueries(['experiences', 'list']);
                setCreateDialogOpen(false);
            },
        }
    );

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
        // TODO: Navigate to editor (Phase 6)
        console.log('Edit experience:', experience.id);
    };

    const handleDelete = (experience) => {
        setDeleteTarget(experience);
    };

    const confirmDelete = () => {
        if (deleteTarget) {
            deleteMutation.mutate(deleteTarget.id);
        }
    };

    return (
        <Box p={3}>
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
                        onClick={() => setCreateDialogOpen(true)}
                    >
                        New Experience
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
                            <Button
                                variant="contained"
                                startIcon={<AddIcon />}
                                onClick={() => setCreateDialogOpen(true)}
                            >
                                New Experience
                            </Button>
                        </Box>
                    ) : (
                        experiences.map((exp) => (
                            <ExperienceCard
                                key={exp.id}
                                experience={exp}
                                onEdit={handleEdit}
                                onDelete={handleDelete}
                                isDeleting={deleteMutation.isLoading && deleteTarget?.id === exp.id}
                            />
                        ))
                    )}
                </Stack>
            )}

            {/* Create Dialog */}
            <CreateExperienceDialog
                open={createDialogOpen}
                onClose={() => setCreateDialogOpen(false)}
                onCreate={(data) => createMutation.mutate(data)}
                isCreating={createMutation.isLoading}
            />

            {/* Delete Confirmation Dialog */}
            <DeleteConfirmDialog
                open={!!deleteTarget}
                experience={deleteTarget}
                onClose={() => setDeleteTarget(null)}
                onConfirm={confirmDelete}
                isDeleting={deleteMutation.isLoading}
            />
        </Box>
    );
}
