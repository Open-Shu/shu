import { Box } from '@mui/material';

import TopBar from '../components/layout/TopBar.jsx';


const UserLayout = ({ children }) => {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <TopBar appBarPosition="static" showAdminLink />
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          bgcolor: 'background.default',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden'
        }}
      >
        {children}
      </Box>
    </Box>
  );
};

export default UserLayout;
