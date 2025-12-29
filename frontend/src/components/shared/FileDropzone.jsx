import React, { useState, useRef, useCallback } from 'react';
import {
  Box,
  Typography,
  LinearProgress,
  IconButton,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  ListItemSecondaryAction,
  Chip,
  useTheme,
  alpha,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  InsertDriveFile as FileIcon,
  CheckCircle as SuccessIcon,
  Error as ErrorIcon,
  Close as RemoveIcon,
} from '@mui/icons-material';

/**
 * FileDropzone - A reusable drag-and-drop file upload component
 *
 * @param {string[]} allowedTypes - Array of allowed file extensions (e.g., ['pdf', 'txt'])
 * @param {number} maxSizeBytes - Maximum file size in bytes
 * @param {boolean} multiple - Allow multiple file selection (default: true)
 * @param {boolean} disabled - Disable the dropzone
 * @param {function} onFilesSelected - Callback with selected files after validation
 * @param {Array} uploadResults - Array of { filename, success, error } for status display
 * @param {boolean} uploading - Whether upload is in progress
 * @param {number} uploadProgress - Upload progress percentage (0-100)
 */
const FileDropzone = ({
  allowedTypes = [],
  maxSizeBytes = 20 * 1024 * 1024,
  multiple = true,
  disabled = false,
  onFilesSelected,
  uploadResults = [],
  uploading = false,
  uploadProgress = 0,
}) => {
  const theme = useTheme();
  const fileInputRef = useRef(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [pendingFiles, setPendingFiles] = useState([]);

  const formatFileSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const validateFile = useCallback((file) => {
    const ext = file.name.split('.').pop()?.toLowerCase() || '';
    if (allowedTypes.length > 0 && !allowedTypes.includes(ext)) {
      return { valid: false, error: `Unsupported type: .${ext}` };
    }
    if (file.size > maxSizeBytes) {
      return { valid: false, error: `Too large: ${formatFileSize(file.size)} > ${formatFileSize(maxSizeBytes)}` };
    }
    return { valid: true };
  }, [allowedTypes, maxSizeBytes]);

  const handleFiles = useCallback((fileList) => {
    const files = Array.from(fileList);
    const validated = files.map((file) => ({
      file,
      ...validateFile(file),
    }));
    setPendingFiles(validated);
    const validFiles = validated.filter((f) => f.valid).map((f) => f.file);
    if (validFiles.length > 0 && onFilesSelected) {
      onFilesSelected(validFiles);
    }
  }, [validateFile, onFilesSelected]);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!disabled) setIsDragOver(true);
  }, [disabled]);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    if (disabled) return;
    const files = e.dataTransfer?.files;
    if (files?.length) handleFiles(files);
  }, [disabled, handleFiles]);

  const handleClick = () => {
    if (!disabled) fileInputRef.current?.click();
  };

  const handleInputChange = (e) => {
    if (e.target.files?.length) {
      handleFiles(e.target.files);
      e.target.value = '';
    }
  };

  const removePendingFile = (index) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const acceptString = allowedTypes.map((t) => `.${t}`).join(',');

  return (
    <Box>
      {/* Drop Zone */}
      <Box
        onClick={handleClick}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        sx={{
          border: `2px dashed ${isDragOver ? theme.palette.primary.main : theme.palette.divider}`,
          borderRadius: 2,
          p: 4,
          textAlign: 'center',
          cursor: disabled ? 'not-allowed' : 'pointer',
          backgroundColor: isDragOver
            ? alpha(theme.palette.primary.main, 0.08)
            : disabled
            ? alpha(theme.palette.action.disabled, 0.04)
            : 'transparent',
          transition: 'all 0.2s ease',
          '&:hover': disabled ? {} : {
            borderColor: theme.palette.primary.light,
            backgroundColor: alpha(theme.palette.primary.main, 0.04),
          },
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple={multiple}
          accept={acceptString}
          onChange={handleInputChange}
          style={{ display: 'none' }}
          disabled={disabled}
        />
        <UploadIcon sx={{ fontSize: 48, color: isDragOver ? 'primary.main' : 'action.active', mb: 1 }} />
        <Typography variant="body1" sx={{ fontWeight: 500, mb: 0.5 }}>
          {isDragOver ? 'Drop files here' : 'Drag & drop files here, or click to select'}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {allowedTypes.length > 0 ? `Allowed: ${allowedTypes.join(', ')}` : 'All file types'} | Max: {formatFileSize(maxSizeBytes)}
        </Typography>
      </Box>

      {/* Upload Progress */}
      {uploading && (
        <Box sx={{ mt: 2 }}>
          <LinearProgress variant="determinate" value={uploadProgress} />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, textAlign: 'center' }}>
            Uploading... {uploadProgress}%
          </Typography>
        </Box>
      )}

      {/* Pending Files List */}
      {pendingFiles.length > 0 && !uploading && uploadResults.length === 0 && (
        <List dense sx={{ mt: 2 }}>
          {pendingFiles.map((item, index) => (
            <ListItem key={index} sx={{ bgcolor: item.valid ? 'transparent' : alpha(theme.palette.error.main, 0.08), borderRadius: 1 }}>
              <ListItemIcon sx={{ minWidth: 36 }}>
                <FileIcon color={item.valid ? 'action' : 'error'} />
              </ListItemIcon>
              <ListItemText
                primary={item.file.name}
                secondary={item.valid ? formatFileSize(item.file.size) : item.error}
                secondaryTypographyProps={{ color: item.valid ? 'text.secondary' : 'error' }}
              />
              <ListItemSecondaryAction>
                {item.valid ? (
                  <Chip label="Ready" size="small" color="success" variant="outlined" />
                ) : (
                  <IconButton size="small" onClick={() => removePendingFile(index)}>
                    <RemoveIcon fontSize="small" />
                  </IconButton>
                )}
              </ListItemSecondaryAction>
            </ListItem>
          ))}
        </List>
      )}

      {/* Upload Results */}
      {uploadResults.length > 0 && (
        <List dense sx={{ mt: 2 }}>
          {uploadResults.map((result, index) => (
            <ListItem key={index} sx={{ bgcolor: result.success ? alpha(theme.palette.success.main, 0.08) : alpha(theme.palette.error.main, 0.08), borderRadius: 1, mb: 0.5 }}>
              <ListItemIcon sx={{ minWidth: 36 }}>
                {result.success ? <SuccessIcon color="success" /> : <ErrorIcon color="error" />}
              </ListItemIcon>
              <ListItemText
                primary={result.filename}
                secondary={result.success
                  ? `${result.word_count || 0} words, ${result.chunk_count || 0} chunks`
                  : result.error
                }
                secondaryTypographyProps={{ color: result.success ? 'text.secondary' : 'error' }}
              />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
};

export default FileDropzone;

