import { alpha } from '@mui/material/styles';
import { keyframes } from '@mui/system';

export const titlePulse = keyframes`
  0% { opacity: 0.7; }
  50% { opacity: 1; }
  100% { opacity: 0.7; }
`;

export const attachmentChipStyles = {
  maxWidth: 240,
  '& .MuiChip-label': {
    display: 'block',
    maxWidth: '100%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
};

export const createChatStyles = (theme) => {
  const isDarkMode = theme.palette.mode === 'dark';

  const conversationHoverBg = alpha(theme.palette.primary.main, isDarkMode ? 0.2 : 0.08);
  const conversationSelectedBg = theme.palette.primary.main;
  const conversationSelectedText = theme.palette.primary.contrastText;
  const conversationBorderColor = alpha(
    isDarkMode ? theme.palette.primary.light : theme.palette.primary.main,
    isDarkMode ? 0.4 : 0.15
  );
  const assistantBubbleBg = isDarkMode
    ? alpha(theme.palette.primary.main, 0.14)
    : theme.palette.background.paper;
  const assistantBubbleBorder = isDarkMode
    ? `1px solid ${alpha(theme.palette.primary.main, 0.35)}`
    : `1px solid ${alpha(theme.palette.primary.main, 0.1)}`;
  const assistantLinkColor = isDarkMode ? theme.palette.info.light : theme.palette.primary.main;
  const userBubbleBg = theme.palette.primary.main;
  const userBubbleText = theme.palette.primary.contrastText;

  return {
    isDarkMode,
    conversationHoverBg,
    conversationSelectedBg,
    conversationSelectedText,
    conversationBorderColor,
    assistantBubbleBg,
    assistantBubbleBorder,
    assistantLinkColor,
    userBubbleBg,
    userBubbleText,
  };
};
