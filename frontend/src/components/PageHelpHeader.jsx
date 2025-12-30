import React, { useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  Collapse,
  IconButton,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  useTheme,
  alpha,
} from '@mui/material';
import {
  Info as InfoIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  TipsAndUpdates as TipIcon,
  ChevronRight as ChevronRightIcon,
} from '@mui/icons-material';

/**
 * PageHelpHeader - A reusable component for displaying page help information
 * 
 * @param {string} title - The page title
 * @param {string} description - Main description of the page's purpose
 * @param {string[]} tips - Array of usage tips or hints
 * @param {React.ReactNode} icon - Optional icon to display next to the title
 * @param {boolean} defaultExpanded - Whether tips are expanded by default (default: false)
 * @param {React.ReactNode} actions - Optional action buttons to display on the right
 */
const PageHelpHeader = ({ 
  title, 
  description, 
  tips = [], 
  icon,
  defaultExpanded = false,
  actions
}) => {
  const [tipsExpanded, setTipsExpanded] = useState(defaultExpanded);
  const theme = useTheme();

  const hasTips = tips && tips.length > 0;

  return (
    <Paper
      elevation={0}
      sx={{
        mb: 3,
        p: 2.5,
        backgroundColor: alpha(theme.palette.info.main, 0.04),
        border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
        borderRadius: 2,
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', flex: 1 }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 40,
              height: 40,
              borderRadius: 1,
              backgroundColor: alpha(theme.palette.info.main, 0.1),
              color: theme.palette.info.main,
              mr: 2,
              flexShrink: 0,
            }}
          >
            {icon || <InfoIcon />}
          </Box>
          <Box sx={{ flex: 1 }}>
            <Typography variant="h6" sx={{ fontWeight: 600, mb: 0.5 }}>
              {title}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6 }}>
              {description}
            </Typography>
          </Box>
        </Box>
        {actions && (
          <Box sx={{ ml: 2, flexShrink: 0 }}>
            {actions}
          </Box>
        )}
      </Box>

      {hasTips && (
        <>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              mt: 2,
              cursor: 'pointer',
              '&:hover': { opacity: 0.8 },
            }}
            onClick={() => setTipsExpanded(!tipsExpanded)}
          >
            <TipIcon sx={{ fontSize: 18, color: theme.palette.warning.main, mr: 1 }} />
            <Typography
              variant="body2"
              sx={{ fontWeight: 500, color: theme.palette.text.secondary }}
            >
              {tipsExpanded ? 'Hide tips' : `${tips.length} helpful tip${tips.length > 1 ? 's' : ''}`}
            </Typography>
            <IconButton size="small" sx={{ ml: 0.5 }}>
              {tipsExpanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
            </IconButton>
          </Box>
          <Collapse in={tipsExpanded}>
            <List dense sx={{ mt: 1, py: 0 }}>
              {tips.map((tip, index) => (
                <ListItem key={index} sx={{ py: 0.25, pl: 0 }}>
                  <ListItemIcon sx={{ minWidth: 28 }}>
                    <ChevronRightIcon fontSize="small" color="action" />
                  </ListItemIcon>
                  <ListItemText
                    primary={tip}
                    primaryTypographyProps={{
                      variant: 'body2',
                      color: 'text.secondary',
                    }}
                  />
                </ListItem>
              ))}
            </List>
          </Collapse>
        </>
      )}
    </Paper>
  );
};

export default PageHelpHeader;

