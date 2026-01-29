import React from "react";
import { useQuery } from "react-query";
import {
  Alert,
  alpha,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  IconButton,
  Link,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import {
  Add as AddIcon,
  Refresh as RefreshIcon,
  SmartToy as BotIcon,
} from "@mui/icons-material";
import { formatDistanceToNow } from "date-fns";
import {
  experiencesAPI,
  extractDataFromResponse,
  formatError,
} from "../services/api";

/**
 * Experience result card using theme-aware styling (like QuickStart SectionCard).
 * Shows: bot icon, title, preview text, relative timestamp, and "View Details" link.
 */
const ExperienceResultCard = ({ experience, onClick }) => {
  const theme = useTheme();
  const hasResult = !!experience.latest_run_id;

  // Generate preview text from prompt and data
  const promptPreview = experience.prompt_template
    ? experience.prompt_template.substring(0, 100)
    : "";
  const dataPreview = experience.result_preview
    ? experience.result_preview.substring(0, 200)
    : "";

  // Format relative time
  const relativeTime = experience.latest_run_finished_at
    ? formatDistanceToNow(new Date(experience.latest_run_finished_at), {
        addSuffix: true,
      })
    : null;

  const handleClick = () => {
    if (onClick) {
      onClick(experience.experience_id);
    }
  };

  return (
    <Card
      elevation={0}
      sx={{
        height: "100%",
        border: `1px solid ${theme.palette.divider}`,
        transition: "all 0.2s ease-in-out",
        backgroundColor: "inherit",
        maxWidth: 360,
        "&:hover": {
          borderColor: theme.palette.primary.main,
          boxShadow: `0 4px 12px ${alpha(theme.palette.primary.main, 0.15)}`,
        },
      }}
    >
      <CardActionArea onClick={handleClick} disabled={!onClick}>
        <CardContent>
          {/* Header: Icon + Title */}
          <Box display="flex" alignItems="center" mb={1.5}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: 36,
                height: 36,
                borderRadius: 1,
                backgroundColor: alpha(theme.palette.primary.main, 0.1),
                color: theme.palette.primary.main,
                mr: 1.5,
              }}
            >
              <BotIcon />
            </Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              {experience.experience_name}
            </Typography>
          </Box>

          {/* Preview: Prompt description */}
          {promptPreview && (
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              {promptPreview}...
            </Typography>
          )}

          {/* Data snippet - shown as code-like block */}
          {hasResult && dataPreview && (
            <>
              <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 0.5 }}>
                {experience.experience_name.toLowerCase().replace(/\s+/g, "_")}
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{
                  fontFamily: "monospace",
                  fontSize: "0.75rem",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  mb: 1.5,
                  backgroundColor: alpha(theme.palette.text.primary, 0.05),
                  p: 1,
                  borderRadius: 1,
                }}
              >
                {dataPreview.length > 100
                  ? dataPreview.substring(0, 100) + "..."
                  : dataPreview}
              </Typography>
            </>
          )}

          {/* Timestamp badge */}
          {relativeTime && (
            <Chip
              icon={
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    bgcolor: theme.palette.success.main,
                    ml: 1,
                  }}
                />
              }
              label={relativeTime}
              size="small"
              sx={{
                bgcolor: "transparent",
                border: `1px solid ${theme.palette.divider}`,
                color: theme.palette.success.main,
                fontSize: "0.75rem",
                mb: 1.5,
                "& .MuiChip-icon": {
                  marginLeft: "8px",
                },
              }}
            />
          )}

          {/* View Details link */}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              color: "primary.main",
            }}
          >
            <Link
              component="button"
              variant="body2"
              onClick={(e) => {
                e.stopPropagation();
                handleClick();
              }}
              sx={{
                fontWeight: 500,
                cursor: "pointer",
                textDecoration: "none",
                "&:hover": {
                  textDecoration: "underline",
                },
              }}
            >
              View Details
            </Link>
          </Box>

          {/* Missing Identities Warning */}
          {!experience.can_run && experience.missing_identities?.length > 0 && (
            <Alert severity="warning" sx={{ mt: 2 }}>
              Missing required connections:{" "}
              {experience.missing_identities.join(", ")}
            </Alert>
          )}
        </CardContent>
      </CardActionArea>
    </Card>
  );
};

export default function ExperienceDashboard({
  onStartChat,
  onCreateConversation,
  createConversationDisabled,
  onExperienceClick,
}) {
  // Fetch user's experience results
  const {
    data: results,
    isLoading,
    error,
    refetch,
  } = useQuery(
    ["my-experience-results"],
    () => experiencesAPI.getMyResults().then(extractDataFromResponse),
    {
      staleTime: 30000,
      refetchInterval: 60000, // Auto-refresh every minute
    },
  );

  const experiences = results?.experiences || [];
  const scheduledCount = results?.scheduled_count || 0;

  const handleCardClick = (experienceId) => {
    if (onExperienceClick) {
      onExperienceClick(experienceId);
    }
  };

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        bgcolor: "background.default",
      }}
    >
      {/* Header */}
      <Box sx={{ p: { xs: 2, sm: 3 }, pb: 0 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 2,
          }}
        >
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 600 }}>
              Dashboard
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {experiences.length} active results • {scheduledCount} scheduled
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <IconButton onClick={() => refetch()} disabled={isLoading}>
              <RefreshIcon />
            </IconButton>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={onCreateConversation}
              disabled={createConversationDisabled}
            >
              New Chat
            </Button>
          </Stack>
        </Box>
      </Box>

      {/* Content */}
      <Box sx={{ flex: 1, p: { xs: 2, sm: 3 }, pt: 2 }}>
        {isLoading && (
          <Box
            display="flex"
            justifyContent="center"
            alignItems="center"
            minHeight={200}
          >
            <CircularProgress />
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formatError(error)}
          </Alert>
        )}

        {!isLoading && !error && experiences.length === 0 && (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
              py: 6,
            }}
          >
            <BotIcon sx={{ fontSize: 80, color: "grey.300", mb: 2 }} />
            <Typography variant="h6" color="text.secondary" gutterBottom>
              No experiences available
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mb: 3, maxWidth: 400 }}
            >
              There are no published experiences yet. Start a new chat to begin
              your conversation.
            </Typography>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={onCreateConversation}
              disabled={createConversationDisabled}
            >
              Start New Chat
            </Button>
          </Box>
        )}

        {!isLoading && !error && experiences.length > 0 && (
          <Box
            sx={{
              display: "flex",
              flexWrap: "wrap",
              gap: 2,
            }}
          >
            {experiences.map((exp) => (
              <ExperienceResultCard
                key={exp.experience_id}
                experience={exp}
                onClick={handleCardClick}
              />
            ))}
          </Box>
        )}
      </Box>
    </Box>
  );
}
