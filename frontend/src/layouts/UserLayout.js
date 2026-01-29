import { Box, IconButton, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { Menu as MenuIcon } from "@mui/icons-material";

import TopBar from "../components/layout/TopBar.jsx";
import {
  MobileSidebarProvider,
  useMobileSidebar,
} from "../contexts/MobileSidebarContext";

const UserLayoutContent = ({ children }) => {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));
  const { toggle } = useMobileSidebar();

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <TopBar
        appBarPosition="static"
        showAdminLink
        hamburgerButton={
          isMobile ? (
            <IconButton
              color="inherit"
              aria-label="open sidebar"
              edge="start"
              onClick={toggle}
              sx={{ mr: 1 }}
            >
              <MenuIcon />
            </IconButton>
          ) : null
        }
      />
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          bgcolor: "background.default",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {children}
      </Box>
    </Box>
  );
};

const UserLayout = ({ children }) => {
  return (
    <MobileSidebarProvider>
      <UserLayoutContent>{children}</UserLayoutContent>
    </MobileSidebarProvider>
  );
};

export default UserLayout;
