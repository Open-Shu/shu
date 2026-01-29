import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "react-query";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  Paper,
  Snackbar,
  Typography,
} from "@mui/material";
import {
  ArrowBack as ArrowBackIcon,
  Chat as ChatIcon,
} from "@mui/icons-material";
import { useTheme } from "@mui/material/styles";
import {
  chatAPI,
  experiencesAPI,
  extractDataFromResponse,
  formatError,
} from "../services/api";
import MarkdownRenderer from "../components/shared/MarkdownRenderer";
import { formatDateTimeFull } from "../utils/timezoneFormatter";
import log from "../utils/log";

/**
 * Full-width page displaying a single experience result.
 * Accessible via /dashboard/experience/:experienceId route.
 */
const ExperienceDetailPage = () => {
  const { experienceId } = useParams();
  const navigate = useNavigate();
  const theme = useTheme();

  // State for conversation creation
  const [isCreatingConversation, setIsCreatingConversation] = useState(false);
  const [errorSnackbar, setErrorSnackbar] = useState({
    open: false,
    message: "",
  });

  // Detect dark mode from theme
  const isDarkMode = theme.palette.mode === "dark";

  // Fetch experience details from my-results endpoint
  const {
    data: results,
    isLoading,
    error,
  } = useQuery(
    ["my-experience-results"],
    () => experiencesAPI.getMyResults().then(extractDataFromResponse),
    {
      staleTime: 30000,
    },
  );

  const handleBack = () => {
    navigate("/dashboard");
  };

  const handleStartConversation = async () => {
    if (!experience?.latest_run_id) {
      setErrorSnackbar({
        open: true,
        message: "No result content available to start conversation",
      });
      return;
    }

    try {
      setIsCreatingConversation(true);

      // Create conversation from experience run
      const response = await chatAPI.createConversationFromExperience(
        experience.latest_run_id,
      );
      const conversation = extractDataFromResponse(response);

      log.info("Started conversation from experience", {
        conversationId: conversation.id,
        runId: experience.latest_run_id,
        experienceId: experience.experience_id,
      });

      // Navigate to conversation view
      navigate(`/chat?conversationId=${conversation.id}`);
    } catch (error) {
      log.error("Failed to start conversation from experience:", error);
      setErrorSnackbar({
        open: true,
        message:
          formatError(error) ||
          "Failed to start conversation. Please try again.",
      });
    } finally {
      setIsCreatingConversation(false);
    }
  };

  const handleCloseErrorSnackbar = () => {
    setErrorSnackbar({ open: false, message: "" });
  };

  // Find the specific experience from results
  const experiences = results?.experiences || [];
  const experience = experiences.find(
    (exp) => exp.experience_id === experienceId,
  );

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {/* Header with back button */}
      <Paper
        sx={{
          p: 2,
          borderRadius: 0,
          borderBottom: 1,
          borderColor: "divider",
          display: "flex",
          alignItems: "center",
          gap: 2,
        }}
      >
        <IconButton onClick={handleBack} aria-label="Back to dashboard">
          <ArrowBackIcon />
        </IconButton>
        <Typography variant="h6" sx={{ fontWeight: 600, flex: 1 }}>
          {experience?.experience_name || "Experience Details"}
        </Typography>
        {experience && (
          <Button
            variant="outlined"
            startIcon={<ChatIcon />}
            onClick={handleStartConversation}
            disabled={
              isCreatingConversation ||
              !experience.latest_run_id ||
              !experience.result_preview
            }
          >
            {isCreatingConversation ? "Starting..." : "Start Conversation"}
          </Button>
        )}
      </Paper>

      {/* Content */}
      <Box sx={{ flex: 1, overflow: "auto", p: { xs: 2, sm: 3 } }}>
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

        {!isLoading && !error && !experience && (
          <Alert severity="warning">
            Experience not found or no results available.
          </Alert>
        )}

        {!isLoading && !error && experience && (
          <Box
            sx={{
              bgcolor: "background.paper",
              borderRadius: 2,
              p: 3,
            }}
          >
            {/* Experience metadata */}
            {experience.latest_run_finished_at && (
              <Box
                sx={{ mb: 3, pb: 2, borderBottom: 1, borderColor: "divider" }}
              >
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  <strong>Generated:</strong>{" "}
                  {experience.trigger_config?.timezone
                    ? formatDateTimeFull(
                        experience.latest_run_finished_at,
                        experience.trigger_config.timezone,
                      )
                    : new Date(
                        experience.latest_run_finished_at,
                      ).toLocaleString()}
                </Typography>
              </Box>
            )}

            <MarkdownRenderer
              content={experience.result_preview}
              isDarkMode={isDarkMode}
            />
          </Box>
        )}
      </Box>

      {/* Error Snackbar */}
      <Snackbar
        open={errorSnackbar.open}
        autoHideDuration={6000}
        onClose={handleCloseErrorSnackbar}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          onClose={handleCloseErrorSnackbar}
          severity="error"
          sx={{ width: "100%" }}
        >
          {errorSnackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default ExperienceDetailPage;
