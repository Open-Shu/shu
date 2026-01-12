import React, { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';

/**
 * Shared markdown renderer with consistent styling across the application.
 * Uses the same rich formatting as chat messages.
 */
const MarkdownRenderer = React.memo(function MarkdownRenderer({
  content,
  isDarkMode,
  parseDocumentHref,
  onOpenDocument,
  sx = {},
}) {
  const theme = useTheme();

  const markdownComponents = useMemo(
    () => ({
      a: ({ href, children, ...props }) => {
        const docTarget = parseDocumentHref?.(href);
        const handleLinkClick = (event) => {
          if (docTarget && typeof onOpenDocument === 'function') {
            event.preventDefault();
            onOpenDocument(docTarget);
          }
        };
        return (
          <a
            href={href}
            target={docTarget ? '_self' : '_blank'}
            rel={docTarget ? undefined : 'noopener noreferrer'}
            onClick={docTarget ? handleLinkClick : undefined}
            {...props}
          >
            {children}
          </a>
        );
      },
      table: ({ children, ...props }) => (
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            margin: '0.5em 0',
          }}
          {...props}
        >
          {children}
        </table>
      ),
      thead: ({ children, ...props }) => (
        <thead
          style={{
            backgroundColor: alpha(
              theme.palette.primary.main,
              isDarkMode ? 0.1 : 0.05
            ),
          }}
          {...props}
        >
          {children}
        </thead>
      ),
      th: ({ children, ...props }) => (
        <th
          style={{
            border: `1px solid ${theme.palette.divider}`,
            padding: '8px 12px',
            backgroundColor: isDarkMode
              ? alpha(theme.palette.primary.main, 0.15)
              : theme.palette.action.hover,
            fontWeight: 'bold',
            textAlign: 'left',
          }}
          {...props}
        >
          {children}
        </th>
      ),
      td: ({ children, ...props }) => (
        <td
          style={{
            border: `1px solid ${theme.palette.divider}`,
            padding: '8px 12px',
            textAlign: 'left',
          }}
          {...props}
        >
          {children}
        </td>
      ),
    }),
    [isDarkMode, onOpenDocument, parseDocumentHref, theme]
  );

  return (
    <Box
      sx={{
        color: theme.palette.text.primary,
        fontWeight: 500,
        '& p': {
          margin: '0.5em 0',
          '&:first-of-type': { marginTop: 0 },
          '&:last-of-type': { marginBottom: 0 },
        },
        '& a': {
          color: theme.palette.primary.main,
          textDecoration: 'underline',
          '&:hover': {
            textDecoration: 'none',
          },
        },
        '& strong': {
          fontWeight: 600,
        },
        '& em': {
          fontStyle: 'italic',
        },
        '& ul, & ol': {
          paddingLeft: '1.5em',
          margin: '0.5em 0',
        },
        '& li': {
          margin: '0.25em 0',
        },
        '& code': {
          backgroundColor: alpha(
            theme.palette.text.primary,
            isDarkMode ? 0.1 : 0.05
          ),
          padding: '0.2em 0.4em',
          borderRadius: '3px',
          fontFamily: 'monospace',
          fontSize: '0.9em',
          wordBreak: 'break-word',
          overflowWrap: 'anywhere',
          maxWidth: '100%',
        },
        '& pre': {
          backgroundColor: alpha(
            theme.palette.text.primary,
            isDarkMode ? 0.12 : 0.05
          ),
          padding: '1em',
          borderRadius: '6px',
          overflow: 'auto',
          margin: '0.5em 0',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          overflowWrap: 'anywhere',
          maxWidth: '100%',
        },
        '& blockquote': {
          borderLeft: `3px solid ${theme.palette.divider}`,
          paddingLeft: '1em',
          margin: '0.5em 0',
          fontStyle: 'italic',
        },
        '& h1, & h2, & h3, & h4, & h5, & h6': {
          margin: '1em 0 0.5em 0',
          '&:first-of-type': { marginTop: 0 },
        },
        '& hr': {
          border: 'none',
          borderTop: `1px solid ${theme.palette.divider}`,
          margin: '1em 0',
        },
        ...sx,
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content || 'No content available.'}
      </ReactMarkdown>
    </Box>
  );
});

export default MarkdownRenderer;