import React, { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box, Chip, Tooltip } from '@mui/material';
import AttachmentIcon from '@mui/icons-material/AttachFile';
import { alpha } from '@mui/material/styles';
import AttachmentPreviewDialog from './AttachmentPreviewDialog';

/**
 * Presentational wrapper around markdown rendering for a single chat message.
 * Handles Shu-specific link interception and historical attachment chips.
 */
const MessageContent = React.memo(function MessageContent({
  message,
  theme,
  isDarkMode,
  userBubbleText,
  assistantLinkColor,
  parseDocumentHref,
  onOpenDocument,
  attachmentChipStyles,
}) {
  const [previewAttachment, setPreviewAttachment] = useState(null);

  const escalationMetadata = useMemo(() => {
    if (message.role !== 'assistant') {
      return null;
    }
    const escalations = message.message_metadata?.rag?.escalations;
    if (!Array.isArray(escalations) || escalations.length === 0) {
      return null;
    }

    const titles = [];
    escalations.forEach((es) => {
      if (Array.isArray(es.docs)) {
        es.docs.forEach((doc) => {
          if (doc?.title) {
            titles.push(doc.title);
          }
        });
      }
    });

    const uniqueTitles = Array.from(new Set(titles));
    return {
      count: escalations.length,
      tooltip:
        uniqueTitles.length > 0
          ? `Escalated: ${uniqueTitles.slice(0, 5).join(', ')}${uniqueTitles.length < titles.length ? '…' : ''
          }`
          : 'Full document escalation',
    };
  }, [message]);

  const markdownComponents = useMemo(
    () => ({
      a: ({ href, children, ...props }) => {
        const docTarget = parseDocumentHref?.(href);
        const handleLinkClick = (event) => {
          if (docTarget) {
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
    <>
      <Box
        sx={{
          color:
            message.role === 'user'
              ? userBubbleText
              : theme.palette.text.primary,
          fontWeight: 500,
          '& p': {
            margin: '0.5em 0',
            '&:first-of-type': { marginTop: 0 },
            '&:last-of-type': { marginBottom: 0 },
          },
          '& a': {
            color:
              message.role === 'user'
                ? alpha(userBubbleText, 0.85)
                : assistantLinkColor,
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
            backgroundColor:
              message.role === 'user'
                ? alpha(userBubbleText, 0.12)
                : alpha(
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
            backgroundColor:
              message.role === 'user'
                ? alpha(userBubbleText, 0.18)
                : alpha(
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
            borderLeft: `3px solid ${message.role === 'user'
              ? alpha(userBubbleText, 0.3)
              : theme.palette.divider
              }`,
            paddingLeft: '1em',
            margin: '0.5em 0',
            fontStyle: 'italic',
          },
        }}
      >
        {escalationMetadata && (
          <Box sx={{ mb: 1 }}>
            <Tooltip title={escalationMetadata.tooltip} placement="top" arrow>
              <Chip
                size="small"
                color="warning"
                label={`Full Document Escalation (${escalationMetadata.count})`}
                sx={{ mr: 1 }}
              />
            </Tooltip>
          </Box>
        )}

        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {message.content}
        </ReactMarkdown>

        {message.role === 'user' &&
          Array.isArray(message.attachments) &&
          message.attachments.length > 0 && (
            <Box sx={{ mt: 1, display: 'flex', flexWrap: 'wrap', gap: 1 }}>
              {message.attachments.map((att) => (
                <Tooltip
                  key={att.id}
                  title={`${att.mime_type} • ${Math.round(att.file_size / 1024)} KB${typeof att.extracted_text_length === 'number'
                    ? ` • text: ${att.extracted_text_length}`
                    : ''
                    }${att.is_ocr ? ' • OCR' : ''}${att.expires_at
                      ? ` • expires ${new Date(att.expires_at).toLocaleString()}`
                      : ''
                    }`}
                >
                  <Chip
                    icon={
                      <AttachmentIcon
                        sx={{
                          color:
                            message.role === 'user'
                              ? userBubbleText
                              : theme.palette.primary.main,
                        }}
                      />
                    }
                    label={
                      att.expired
                        ? `${att.original_filename} (expired)`
                        : att.original_filename
                    }
                    variant="outlined"
                    size="small"
                    clickable={!att.expired}
                    onClick={() => {
                      if (!att.expired) {
                        setPreviewAttachment(att);
                      }
                    }}
                    sx={{
                      ...attachmentChipStyles,
                      ...(att.expired ? { opacity: 0.7, borderStyle: 'dashed' } : {}),
                      cursor: att.expired ? 'default' : 'pointer',
                      borderColor:
                        message.role === 'user'
                          ? alpha(userBubbleText, 0.6)
                          : alpha(theme.palette.primary.main, 0.4),
                      color:
                        message.role === 'user'
                          ? userBubbleText
                          : theme.palette.text.primary,
                      backgroundColor:
                        message.role === 'user'
                          ? alpha(userBubbleText, 0.12)
                          : alpha(theme.palette.primary.main, 0.08),
                    }}
                    color={
                      att.expired
                        ? 'default'
                        : message.role === 'user'
                          ? 'default'
                          : 'primary'
                    }
                  />
                </Tooltip>
              ))}
            </Box>
          )}
      </Box>

      <AttachmentPreviewDialog
        open={Boolean(previewAttachment)}
        onClose={() => setPreviewAttachment(null)}
        attachment={previewAttachment}
      />
    </>
  );
});

export default MessageContent;
