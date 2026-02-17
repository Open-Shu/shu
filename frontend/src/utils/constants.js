import { mergeDeep } from './objectUtils';
import { resolveBranding, derivePrimaryVariants } from './brandingUtils';

const primaryMain = '#2E5A87';
const primaryLight = '#4A7BA7';
const primaryDark = '#1E3A5F';
const accentRed = '#E53E3E';
const neutralGray = '#6B7280';

export const lightThemeBase = {
  palette: {
    mode: 'light',
    primary: {
      main: primaryMain,
      light: primaryLight,
      dark: primaryDark,
      contrastText: '#ffffff',
    },
    secondary: {
      main: accentRed,
      light: '#F56565',
      dark: '#C53030',
      contrastText: '#ffffff',
    },
    background: {
      default: '#F8FAFC',
      paper: '#FFFFFF',
    },
    text: {
      primary: '#1A202C',
      secondary: neutralGray,
    },
    divider: '#E2E8F0',
    grey: {
      50: '#F7FAFC',
      100: '#EDF2F7',
      200: '#E2E8F0',
      300: '#CBD5E0',
      400: '#A0AEC0',
      500: neutralGray,
      600: '#4A5568',
      700: '#2D3748',
      800: '#1A202C',
      900: '#171923',
    },
  },
  typography: {
    fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    h1: {
      fontWeight: 700,
      fontSize: '2.5rem',
      lineHeight: 1.2,
      color: '#1A202C',
    },
    h2: {
      fontWeight: 600,
      fontSize: '2rem',
      lineHeight: 1.3,
      color: '#1A202C',
    },
    h3: {
      fontWeight: 600,
      fontSize: '1.5rem',
      lineHeight: 1.4,
      color: '#1A202C',
    },
    h4: {
      fontWeight: 600,
      fontSize: '1.25rem',
      lineHeight: 1.4,
      color: '#1A202C',
    },
    h5: {
      fontWeight: 600,
      fontSize: '1.125rem',
      lineHeight: 1.4,
      color: '#1A202C',
    },
    h6: {
      fontWeight: 600,
      fontSize: '1rem',
      lineHeight: 1.4,
      color: '#1A202C',
    },
    body1: {
      fontSize: '1rem',
      lineHeight: 1.6,
      color: '#1A202C',
    },
    body2: {
      fontSize: '0.875rem',
      lineHeight: 1.6,
      color: neutralGray,
    },
    button: {
      fontWeight: 600,
      textTransform: 'none',
    },
  },
  shape: {
    borderRadius: 8,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          fontWeight: 600,
          textTransform: 'none',
          boxShadow: 'none',
          '&:hover': {
            boxShadow: '0 4px 12px rgba(46, 90, 135, 0.15)',
          },
        },
        contained: {
          '&:hover': {
            boxShadow: '0 4px 12px rgba(46, 90, 135, 0.25)',
          },
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: 12,
          boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1), 0 1px 2px rgba(0, 0, 0, 0.06)',
          '&:hover': {
            boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1), 0 2px 4px rgba(0, 0, 0, 0.06)',
          },
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: primaryMain,
          color: '#FFFFFF',
          boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1)',
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundColor: '#FFFFFF',
          color: '#1A202C',
          borderRight: '1px solid #E2E8F0',
        },
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          margin: '4px 8px',
          color: '#1A202C',
          '&.Mui-selected': {
            backgroundColor: primaryMain,
            color: '#FFFFFF',
            '&:hover': {
              backgroundColor: primaryDark,
            },
            '& .MuiListItemIcon-root': {
              color: '#FFFFFF',
            },
            '& .MuiListItemText-primary': {
              color: '#FFFFFF',
            },
          },
          '&:hover': {
            backgroundColor: '#F7FAFC',
          },
          '& .MuiListItemIcon-root': {
            color: primaryMain,
          },
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 6,
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            borderRadius: 8,
          },
          '& .MuiInputLabel-root': {
            fontSize: '0.875rem',
            color: '#6B7280',
            fontWeight: 500,
            '&.Mui-focused': {
              color: '#2E5A87',
            },
            '&.MuiInputLabel-shrink': {
              fontSize: '0.75rem',
              color: '#6B7280',
              fontWeight: 500,
              transform: 'translate(14px, -9px) scale(1)',
            },
          },
          '& .MuiInputBase-input::placeholder': {
            color: '#9CA3AF',
            opacity: 0.7,
            fontStyle: 'italic',
          },
          '& .MuiInputBase-input::-webkit-input-placeholder': {
            color: '#9CA3AF',
            opacity: 0.7,
            fontStyle: 'italic',
          },
          '& .MuiInputBase-input::-moz-placeholder': {
            color: '#9CA3AF',
            opacity: 0.7,
            fontStyle: 'italic',
          },
        },
      },
    },
    MuiFormControl: {
      styleOverrides: {
        root: {
          '& .MuiInputLabel-root': {
            fontSize: '0.875rem',
            color: '#6B7280',
            fontWeight: 500,
            backgroundColor: '#FFFFFF',
            paddingLeft: '4px',
            paddingRight: '4px',
            '&.Mui-focused': {
              color: '#2E5A87',
              backgroundColor: '#FFFFFF',
            },
            '&.MuiInputLabel-shrink': {
              fontSize: '0.75rem',
              color: '#6B7280',
              fontWeight: 500,
              backgroundColor: '#FFFFFF',
              transform: 'translate(14px, -9px) scale(1)',
            },
          },
          '& .MuiOutlinedInput-root': {
            borderRadius: 8,
          },
        },
      },
    },
    MuiSelect: {
      styleOverrides: {
        root: {
          borderRadius: 8,
        },
      },
    },
    MuiInputLabel: {
      styleOverrides: {
        root: {
          fontSize: '0.875rem',
          color: '#6B7280',
          fontWeight: 500,
          backgroundColor: '#FFFFFF',
          paddingLeft: '4px',
          paddingRight: '4px',
          '&.Mui-focused': {
            color: '#2E5A87',
            backgroundColor: '#FFFFFF',
          },
          '&.MuiInputLabel-shrink': {
            fontSize: '0.75rem',
            color: '#6B7280',
            fontWeight: 500,
            backgroundColor: '#FFFFFF',
            transform: 'translate(14px, -9px) scale(1)',
          },
        },
      },
    },
  },
};

export const darkThemeBase = {
  palette: {
    mode: 'dark',
    primary: {
      main: '#2E5A87',
      light: '#4A7BA7',
      dark: '#1E3A5F',
      contrastText: '#ffffff',
    },
    secondary: {
      main: '#f85149',
      light: '#ff6b6b',
      dark: '#da3633',
      contrastText: '#0d1117',
    },
    background: {
      default: '#0d1117',
      paper: '#161b22',
    },
    text: {
      primary: '#f0f6fc',
      secondary: '#8b949e',
    },
    divider: '#30363d',
    grey: {
      50: '#21262d',
      100: '#30363d',
      200: '#484f58',
      300: '#6e7681',
      400: '#8b949e',
      500: '#8b949e',
      600: '#c9d1d9',
      700: '#f0f6fc',
      800: '#f0f6fc',
      900: '#ffffff',
    },
  },
  typography: {
    fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    h1: {
      fontWeight: 700,
      fontSize: '2.5rem',
      lineHeight: 1.2,
      color: '#f0f6fc',
    },
    h2: {
      fontWeight: 600,
      fontSize: '2rem',
      lineHeight: 1.3,
      color: '#f0f6fc',
    },
    h3: {
      fontWeight: 600,
      fontSize: '1.5rem',
      lineHeight: 1.4,
      color: '#f0f6fc',
    },
    h4: {
      fontWeight: 600,
      fontSize: '1.25rem',
      lineHeight: 1.4,
      color: '#f0f6fc',
    },
    h5: {
      fontWeight: 600,
      fontSize: '1.125rem',
      lineHeight: 1.4,
      color: '#f0f6fc',
    },
    h6: {
      fontWeight: 600,
      fontSize: '1rem',
      lineHeight: 1.4,
      color: '#f0f6fc',
    },
    body1: {
      fontSize: '1rem',
      lineHeight: 1.6,
      color: '#f0f6fc',
    },
    body2: {
      fontSize: '0.875rem',
      lineHeight: 1.6,
      color: '#8b949e',
    },
    button: {
      fontWeight: 600,
      textTransform: 'none',
    },
  },
  shape: {
    borderRadius: 8,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          fontWeight: 600,
          textTransform: 'none',
          boxShadow: 'none',
          '&:hover': {
            boxShadow: '0 4px 12px rgba(88, 166, 255, 0.25)',
          },
        },
        contained: {
          '&:hover': {
            boxShadow: '0 4px 12px rgba(88, 166, 255, 0.35)',
          },
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: 12,
          backgroundColor: '#161b22',
          border: '1px solid #30363d',
          boxShadow: '0 1px 3px rgba(1, 4, 9, 0.6), 0 1px 2px rgba(1, 4, 9, 0.4)',
          '&:hover': {
            boxShadow: '0 4px 6px rgba(1, 4, 9, 0.6), 0 2px 4px rgba(1, 4, 9, 0.4)',
          },
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#161b22',
          color: '#f0f6fc',
          boxShadow: '0 1px 3px rgba(0, 0, 0, 0.4)',
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundColor: '#161b22',
          color: '#f0f6fc',
          borderRight: '1px solid #21262d',
        },
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          margin: '4px 8px',
          color: '#f0f6fc',
          '&.Mui-selected': {
            backgroundColor: '#2E5A87',
            color: '#ffffff',
            '&:hover': {
              backgroundColor: '#1E3A5F',
            },
            '& .MuiListItemIcon-root': {
              color: '#ffffff',
            },
            '& .MuiListItemText-primary': {
              color: '#ffffff',
            },
          },
          '&:hover': {
            backgroundColor: '#21262d',
          },
          '& .MuiListItemIcon-root': {
            color: '#4A7BA7',
          },
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 6,
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            borderRadius: 8,
            backgroundColor: '#0d1117',
          },
          '& .MuiInputLabel-root': {
            fontSize: '0.875rem',
            color: '#8b949e',
            fontWeight: 500,
            '&.Mui-focused': {
              color: '#2E5A87',
            },
            '&.MuiInputLabel-shrink': {
              fontSize: '0.75rem',
              color: '#8b949e',
              fontWeight: 500,
              transform: 'translate(14px, -9px) scale(1)',
            },
          },
          '& .MuiInputBase-input::placeholder': {
            color: '#8b949e',
            opacity: 0.7,
            fontStyle: 'italic',
          },
          '& .MuiInputBase-input::-webkit-input-placeholder': {
            color: '#8b949e',
            opacity: 0.7,
            fontStyle: 'italic',
          },
          '& .MuiInputBase-input::-moz-placeholder': {
            color: '#8b949e',
            opacity: 0.7,
            fontStyle: 'italic',
          },
        },
      },
    },
    MuiFormControl: {
      styleOverrides: {
        root: {
          '& .MuiInputLabel-root': {
            fontSize: '0.875rem',
            color: '#8b949e',
            fontWeight: 500,
            backgroundColor: '#161b22',
            paddingLeft: '4px',
            paddingRight: '4px',
            '&.Mui-focused': {
              color: '#2E5A87',
              backgroundColor: '#161b22',
            },
            '&.MuiInputLabel-shrink': {
              fontSize: '0.75rem',
              color: '#8b949e',
              fontWeight: 500,
              backgroundColor: '#161b22',
              transform: 'translate(14px, -9px) scale(1)',
            },
          },
          '& .MuiOutlinedInput-root': {
            borderRadius: 8,
            backgroundColor: '#0d1117',
          },
        },
      },
    },
    MuiSelect: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          backgroundColor: '#0d1117',
        },
      },
    },
    MuiInputLabel: {
      styleOverrides: {
        root: {
          fontSize: '0.875rem',
          color: '#8b949e',
          fontWeight: 500,
          backgroundColor: '#161b22',
          paddingLeft: '4px',
          paddingRight: '4px',
          '&.Mui-focused': {
            color: '#2E5A87',
            backgroundColor: '#161b22',
          },
          '&.MuiInputLabel-shrink': {
            fontSize: '0.75rem',
            color: '#8b949e',
            fontWeight: 500,
            backgroundColor: '#161b22',
            transform: 'translate(14px, -9px) scale(1)',
          },
        },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: '#0F172A',
          color: '#F8FAFC',
          fontSize: '0.75rem',
          padding: '6px 10px',
          borderRadius: 6,
        },
        arrow: {
          color: '#0F172A',
        },
      },
    },
  },
};

// Re-export branding utilities from dedicated module for backward compatibility
export {
  defaultBranding,
  resolveBranding,
  getBrandingFaviconUrl,
  getBrandingAppName,
  getBrandingFaviconUrlForTheme,
  getTopbarTextColor,
  derivePrimaryVariants,
} from './brandingUtils';

export const getThemeConfig = (mode = 'light', branding) => {
  const resolved = resolveBranding(branding);
  const base = mode === 'dark' ? darkThemeBase : lightThemeBase;
  const overrides = mode === 'dark' ? resolved.darkThemeOverrides : resolved.lightThemeOverrides;
  const config = mergeDeep(base, overrides);

  // When branding overrides primary.main, the light/dark variants still hold
  // the base-theme defaults. Regenerate them so the full palette stays consistent.
  const pm = config.palette.primary.main;
  const { lighter, darker } = derivePrimaryVariants(pm);
  config.palette.primary.light = lighter;
  config.palette.primary.dark = darker;

  // Sync MuiListItemButton overrides with the resolved primary palette.
  const listBtn = config.components?.MuiListItemButton?.styleOverrides?.root;
  if (listBtn) {
    if (listBtn['&.Mui-selected']) {
      listBtn['&.Mui-selected'].backgroundColor = pm;
      if (listBtn['&.Mui-selected']['&:hover']) {
        listBtn['&.Mui-selected']['&:hover'].backgroundColor = darker;
      }
    }
    if (listBtn['& .MuiListItemIcon-root']) {
      listBtn['& .MuiListItemIcon-root'].color = pm;
    }
  }

  return config;
};

export const getPrimaryColor = (mode = 'light', branding) => {
  const theme = getThemeConfig(mode, branding);
  return theme?.palette?.primary?.main ?? primaryMain;
};

export const DEFAULT_THEME_COLOR = lightThemeBase.palette.primary.main;

export const RAG_REWRITE_OPTIONS = [
  { value: 'raw_query', label: 'Raw Query (pass-through)' },
  { value: 'distill_context', label: 'Distill Query (key facts only)' },
  { value: 'rewrite_enhanced', label: 'Rewrite & Enhance (LLM optimized)' },
];
