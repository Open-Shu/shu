import React from "react";
import { useNavigate } from "react-router-dom";
import { Box } from "@mui/material";
import ExperienceDashboard from "../components/ExperienceDashboard";

/**
 * Standalone Dashboard page that displays experience results.
 * Accessible via /dashboard route.
 */
const DashboardPage = () => {
  const navigate = useNavigate();

  const handleCreateConversation = () => {
    navigate("/chat");
  };

  const handleExperienceClick = (experienceId) => {
    navigate(`/dashboard/experience/${experienceId}`);
  };

  return (
    <Box sx={{ height: "100%", overflow: "auto" }}>
      <ExperienceDashboard
        onCreateConversation={handleCreateConversation}
        createConversationDisabled={false}
        onExperienceClick={handleExperienceClick}
      />
    </Box>
  );
};

export default DashboardPage;
