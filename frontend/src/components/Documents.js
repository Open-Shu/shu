import React, { useState, useEffect, useCallback, memo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import {
  Box,
  Typography,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Chip,
  Button,
  Alert,
  CircularProgress,
  Tooltip,
  TextField,
  InputAdornment,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Grid,
  TablePagination,
  Tabs,
  Tab,
  Collapse,
} from '@mui/material';
import {
  Visibility as PreviewIcon,
  Search as SearchIcon,
  Refresh as RefreshIcon,
  ArrowBack as BackIcon,
  CloudUpload as UploadIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  Description as DocumentIcon,
} from '@mui/icons-material';
import KBPluginFeedsTab from './KBPluginFeedsTab';
import DocumentPreview from './DocumentPreview';
import FileDropzone from './shared/FileDropzone';
import PageHelpHeader from './PageHelpHeader';
import { knowledgeBaseAPI, extractDataFromResponse, formatError } from '../services/api';
import { configService } from '../services/config';

const SearchFilter = memo(function SearchFilter({searchQuery, setSearchQuery, filterBy, setFilterBy, fetchDocuments, setPage}) {
  return <Paper sx={{ p: 2, mb: 3 }}>
    <Grid container spacing={2} alignItems="center">
      <Grid item xs={12} md={6}>
        <TextField
          fullWidth
          placeholder="Search documents..."
          value={searchQuery}
          onChange={(e) => { setSearchQuery(e.target.value); setPage(0); }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon />
              </InputAdornment>
            ),
          }}
        />
      </Grid>
      <Grid item xs={12} md={3}>
        <FormControl fullWidth>
          <InputLabel>Filter by</InputLabel>
          <Select
            value={filterBy}
            onChange={(e) => { setFilterBy(e.target.value); setPage(0); }}
            label="Filter by"
          >
            <MenuItem value="all">All Documents</MenuItem>
            <MenuItem value="ocr">OCR Processed</MenuItem>
            <MenuItem value="text">Text Extracted</MenuItem>
            <MenuItem value="high-confidence">High Confidence (≥80%)</MenuItem>
            <MenuItem value="low-confidence">Low Confidence (&lt;60%)</MenuItem>
          </Select>
        </FormControl>
      </Grid>
      <Grid item xs={12} md={3}>
        <Button
          fullWidth
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={() => fetchDocuments()}
        >
          Refresh
        </Button>
      </Grid>
    </Grid>
  </Paper>;
});

const DocumentResults = function DocumentResults({
  documents,
  setSelectedDocument,
  setPreviewOpen,
  setPage,
  setRowsPerPage,
  totalDocuments,
  page,
  rowsPerPage,
  searchQuery,
  filterBy,
  loading
}) {
  const handlePreview = (document) => {
    setSelectedDocument(document);
    setPreviewOpen(true);
  };

  const handleChangePage = (event, newPage) => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event) => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  const getExtractionMethodColor = (method) => {
    switch (method?.toLowerCase()) {
      case 'ocr': return 'warning';
      case 'text': return 'success';
      case 'pdfplumber': return 'info';
      case 'pymupdf': return 'primary';
      default: return 'default';
    }
  };

  const getConfidenceColor = (confidence) => {
    if (!confidence) return 'default';
    if (confidence >= 0.8) return 'success';
    if (confidence >= 0.6) return 'warning';
    return 'error';
  };

  const formatFileSize = (bytes) => {
    if (!bytes) return 'N/A';
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${sizes[i]}`;
  };

  const formatDate = (dateString) => {
    if (!dateString) return 'N/A';
    return new Date(dateString).toLocaleDateString();
  };


  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return documents.length > 0 ? (
    <>
      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Document</TableCell>
              <TableCell>File Type</TableCell>
              <TableCell>Size</TableCell>
              <TableCell>Extraction Method</TableCell>
              <TableCell>Confidence</TableCell>
              <TableCell>Processing Time</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Created</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {documents.map((doc) => (
              <TableRow key={doc.id} hover>
                <TableCell>
                  <Box>
                    <Typography variant="subtitle2" fontWeight="medium">
                      {doc.title}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {doc.character_count?.toLocaleString() || 0} characters
                    </Typography>
                  </Box>
                </TableCell>
                <TableCell>
                  <Chip
                    label={doc.file_type?.toUpperCase() || 'Unknown'}
                    size="small"
                    variant="outlined"
                  />
                </TableCell>
                <TableCell>
                  <Typography variant="body2">
                    {formatFileSize(doc.file_size)}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Box>
                    <Chip
                      label={doc.extraction_method || 'Unknown'}
                      color={getExtractionMethodColor(doc.extraction_method)}
                      size="small"
                    />
                    {doc.extraction_engine && doc.extraction_engine !== doc.extraction_method && (
                      <Typography variant="caption" display="block" color="text.secondary" sx={{ mt: 0.5 }}>
                        {doc.extraction_engine}
                      </Typography>
                    )}
                  </Box>
                </TableCell>
                <TableCell>
                  {doc.extraction_confidence ? (
                    <Chip
                      label={`${(doc.extraction_confidence * 100).toFixed(1)}%`}
                      color={getConfidenceColor(doc.extraction_confidence)}
                      size="small"
                    />
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      N/A
                    </Typography>
                  )}
                </TableCell>
                <TableCell>
                  <Typography variant="body2">
                    {doc.extraction_duration ? `${(doc.extraction_duration).toFixed(2)}s` : 'N/A'}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Chip
                    label={doc.processing_status}
                    color={doc.processing_status === 'processed' ? 'success' : 'default'}
                    size="small"
                  />
                </TableCell>
                <TableCell>
                  <Typography variant="body2">
                    {formatDate(doc.created_at)}
                  </Typography>
                </TableCell>
                <TableCell align="right">
                  <Tooltip title="Preview Document">
                    <IconButton
                      onClick={() => handlePreview(doc)}
                      size="small"
                    >
                      <PreviewIcon />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <TablePagination
        component="div"
        count={totalDocuments}
        page={page}
        onPageChange={handleChangePage}
        rowsPerPage={rowsPerPage}
        onRowsPerPageChange={handleChangeRowsPerPage}
        rowsPerPageOptions={[10, 25, 50, 100]}
      />
    </>
  ) : (
    <Alert severity="info">
      {searchQuery || filterBy !== 'all'
        ? 'No documents match your search criteria.'
        : 'No documents found in this knowledge base.'
      }
    </Alert>
  );
}

function Documents() {
  const { kbId } = useParams();
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [selectedDocument, setSelectedDocument] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [filterBy, setFilterBy] = useState('all');
  const [knowledgeBase, setKnowledgeBase] = useState(null);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);
  const [totalDocuments, setTotalDocuments] = useState(0);

  // Upload state
  const [uploadExpanded, setUploadExpanded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [ingesting, setIngesting] = useState(false); // True when upload done but server processing
  const [uploadResults, setUploadResults] = useState([]);
  const [uploadError, setUploadError] = useState(null);

  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = ((searchParams.get('tab') || '') === 'feeds') ? 1 : 0;
  const [tab, setTab] = useState(initialTab);

  // Get KB-specific upload restrictions (text extraction only, no image OCR)
  const uploadRestrictions = configService.getKbUploadRestrictions();

  useEffect(() => {
    const desired = tab === 1 ? 'feeds' : 'documents';
    const current = searchParams.get('tab') || 'documents';
    if (current !== desired) {
      const sp = new URLSearchParams(searchParams);
      sp.set('tab', desired);
      setSearchParams(sp, { replace: true });
    }
  }, [tab, searchParams, setSearchParams]);

  const fetchKnowledgeBase = React.useCallback(async () => {
    if (!kbId) return;
    try {
      const response = await knowledgeBaseAPI.get(kbId);
      const data = extractDataFromResponse(response);
      setKnowledgeBase(data);
    } catch (err) {
      setError(`Failed to load knowledge base: ${err.message || 'Unknown error'}`);
    }
  }, [kbId]);

  // Debounce search input to add lag to the API call
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery.trim().toLowerCase());
    }, 300); // 300ms delay

    return () => clearTimeout(timer);
  }, [searchQuery]);

  const fetchDocuments = useCallback(async (pageNum = page, pageSize = rowsPerPage) => {
    if (!kbId) return;
    setLoading(true);
    try {
      const response = await knowledgeBaseAPI.getDocuments(kbId, {
        limit: pageSize,
        offset: pageNum * pageSize,
        search_query: debouncedSearchQuery,
        filter_by: filterBy,
      });
      const data = extractDataFromResponse(response);
      const items = data.items || [];
      const total = typeof data.total === 'number' ? data.total : items.length;
      setDocuments(items);
      setTotalDocuments(total);
    } catch (err) {
      setError(`Failed to load documents: ${err.message || 'Unknown error'}`);
    } finally {
      setLoading(false);
    }
  }, [kbId, page, rowsPerPage, debouncedSearchQuery, filterBy]);

  useEffect(() => {
    fetchKnowledgeBase();
    fetchDocuments();
  }, [fetchKnowledgeBase, fetchDocuments]);

  // Refetch when pagination or filters change
  useEffect(() => {
    fetchDocuments();
  }, [page, rowsPerPage, debouncedSearchQuery, filterBy, fetchDocuments]);

  const handleClosePreview = () => {
    setPreviewOpen(false);
    setSelectedDocument(null);
  };

  const handleFilesSelected = useCallback(async (files) => {
    if (!files.length || !kbId) return;

    setUploading(true);
    setUploadProgress(0);
    setIngesting(false);
    setUploadResults([]);
    setUploadError(null);

    try {
      const response = await knowledgeBaseAPI.uploadDocuments(
        kbId,
        files,
        (progressEvent) => {
          const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
          setUploadProgress(percent);
          // When upload reaches 100%, switch to ingesting state
          if (percent >= 100) {
            setIngesting(true);
          }
        }
      );

      const data = extractDataFromResponse(response);
      setUploadResults(data.results || []);

      // Refresh documents list if any uploads succeeded
      if (data.successful > 0) {
        fetchDocuments();
      }
    } catch (err) {
      setUploadError(formatError(err));
    } finally {
      setUploading(false);
      setIngesting(false);
    }
  }, [kbId, fetchDocuments]);

  if (error) {
    return (
      <Box p={3}>
        <Alert severity="error">{error}</Alert>
      </Box>
    );
  }

  return (
    <Box p={3}>
      {/* Header */}
      <Box display="flex" alignItems="center" mb={3}>
        <Button
          startIcon={<BackIcon />}
          onClick={() => window.history.back()}
          sx={{ mr: 2 }}
        >
          Back
        </Button>
        <Box>
          <Typography variant="h4" gutterBottom>
            Documents
          </Typography>
          {knowledgeBase && (
            <Typography variant="body1" color="text.secondary">
              Knowledge Base: {knowledgeBase.name}
            </Typography>
          )}
        </Box>
      </Box>

      {/* KB Tabs */}
      <Tabs value={tab} onChange={(_e, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Documents" />
        <Tab label="Plugin Feeds" />
      </Tabs>

      {tab === 1 && (
        <KBPluginFeedsTab knowledgeBaseId={kbId} />
      )}

      {tab === 0 && (<>
        <PageHelpHeader
          title="Documents in this Knowledge Base"
          description="Documents are the content that powers RAG retrieval. Each document is automatically chunked and embedded for semantic search. Upload files directly or configure Plugin Feeds for automated ingestion."
          icon={<DocumentIcon />}
          tips={[
            'Upload documents using the dropzone below or configure Plugin Feeds for automated sync',
            'Supported formats include PDF, DOC, TXT, HTML, and more',
            'Use the preview button to inspect document content and chunk boundaries',
            'Filter by status to find documents that failed processing or need attention',
          ]}
        />

        {/* Upload Section */}
        <Paper sx={{ p: 2, mb: 3, mt: 2 }}>
          <Box
            display="flex"
            alignItems="center"
            justifyContent="space-between"
            onClick={() => setUploadExpanded(!uploadExpanded)}
            sx={{ cursor: 'pointer' }}
          >
            <Box display="flex" alignItems="center" gap={1}>
              <UploadIcon color="primary" />
              <Typography variant="subtitle1" fontWeight={500}>
                Upload Documents
              </Typography>
            </Box>
            <IconButton size="small">
              {uploadExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </IconButton>
          </Box>
          <Collapse in={uploadExpanded}>
            <Box sx={{ mt: 2 }}>
              {uploadError && (
                <Alert severity="error" sx={{ mb: 2 }} onClose={() => setUploadError(null)}>
                  {uploadError}
                </Alert>
              )}
              <FileDropzone
                allowedTypes={uploadRestrictions.allowed_types}
                maxSizeBytes={uploadRestrictions.max_size_bytes}
                multiple
                disabled={uploading || ingesting}
                onFilesSelected={handleFilesSelected}
                uploadResults={uploadResults}
                uploading={uploading}
                uploadProgress={uploadProgress}
                ingesting={ingesting}
              />
            </Box>
          </Collapse>
        </Paper>

        <SearchFilter
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          filterBy={filterBy}
          setFilterBy={setFilterBy}
          fetchDocuments={fetchDocuments}
          setPage={setPage}
        />

        <DocumentResults
          documents={documents}
          setSelectedDocument={setSelectedDocument}
          setPreviewOpen={setPreviewOpen}
          setPage={setPage}
          setRowsPerPage={setRowsPerPage}
          totalDocuments={totalDocuments}
          page={page}
          rowsPerPage={rowsPerPage}
          searchQuery={searchQuery}
          filterBy={filterBy}
          loading={loading}
        />
      </>)}

      {/* Document Preview Dialog */}
      {selectedDocument && (
        <DocumentPreview
          open={previewOpen}
          onClose={handleClosePreview}
          kbId={kbId}
          documentId={selectedDocument.id}
          maxChars={2000}
        />
      )}


    </Box>
  );
}

export default Documents;