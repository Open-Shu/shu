import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Button,
  Card,
  CardContent,
  Grid,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  Alert,
  CircularProgress,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
} from '@mui/material';
import { Visibility as EyeIcon, PlayArrow as RunIcon, Chat as ChatIcon } from '@mui/icons-material';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { extractDataFromResponse } from '../services/api';
import { agentsAPI, modelConfigAPI, chatAPI } from '../services/api';
import { log } from '../utils/log';

const JsonViewer = ({ open, onClose, title, data }) => (
  <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
    <DialogTitle>{title}</DialogTitle>
    <DialogContent dividers>
      <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>
        {JSON.stringify(data, null, 2)}
      </pre>
    </DialogContent>
  </Dialog>
);

const Section = ({ title, entry, onViewRaw, children }) => {
  const ok = entry?.ok === true;
  const summary = entry?.summary || (ok ? 'No summary provided' : (entry?.error || 'Not configured or unavailable'));
  const color = ok ? 'success' : 'warning';

  return (
    <Card>
      <CardContent>
        <Box display="flex" alignItems="center" justifyContent="space-between" mb={1}>
          <Typography variant="h6">{title}</Typography>
          <IconButton size="small" onClick={onViewRaw} title="View raw JSON">
            <EyeIcon />
          </IconButton>
        </Box>
        <Alert severity={color} sx={{ mb: 1 }}>
          <Box sx={{
            '& p': { m: 0 },
            '& ul, & ol': { pl: 3, my: 0.5 }
          }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary}</ReactMarkdown>
          </Box>
        </Alert>
        {children}
      </CardContent>
    </Card>
  );
};

const GChatSection = ({ entry, onViewRaw }) => (
  <Section title="Google Chat" entry={entry} onViewRaw={onViewRaw} />
);

export default function MorningBriefing() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [viewer, setViewer] = useState({ open: false, title: '', data: null });
  const [modelConfigurationId, setModelConfigurationId] = useState('');
  const [modelConfigs, setModelConfigs] = useState([]);

  useEffect(() => {
    (async () => {
      try {
        const resp = await modelConfigAPI.list({ active_only: true });
        const data = extractDataFromResponse(resp);
        const items = Array.isArray(data?.items) ? data.items : [];
        setModelConfigs(items);
      } catch (e) {
        log.error('Failed to load Model Configurations for Morning Briefing', e);
      }
    })();
  }, []);

  const runBriefing = async () => {
    setLoading(true);
    setError(null);
    try {
      if (!modelConfigurationId) {
        setError('Please select a Model Configuration');
        setLoading(false);
        return;
      }
      const body = {
        model_configuration_id: modelConfigurationId,
        // Server will default impersonation to the logged-in user
        calendar_events: { since_hours: 24 },
        // Fetch the past week of emails to include the full 7-day window
        gmail_digest: { since_hours: 168, max_results: 50 },
        // Include recent Google Chat messages
        gchat_digest: { since_hours: 168, max_results: 1000 },

      };
      const resp = await agentsAPI.runMorningBriefing(body, 300000); // extend timeout to 5 minutes for demo
      const data = extractDataFromResponse(resp);
      setResult(data);
    } catch (e) {
      setError(e?.response?.data?.error?.message || e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleChatAboutSummary = async () => {
    if (!modelConfigurationId) {
      setError('Please select a Model Configuration');
      return;
    }
    if (!result) {
      setError('Run Morning Briefing first to generate a summary');
      return;
    }
    try {
      setChatLoading(true);
      const title = `Morning Briefing ${new Date().toLocaleDateString()}`;
      const convResp = await chatAPI.createConversationWithModelConfig({
        model_configuration_id: modelConfigurationId,
        title,
      });
      const conv = extractDataFromResponse(convResp);
      const artifacts = result?.artifacts || {};


      const intro = "Let's discuss today's Morning Briefing. I've included the briefing summary below.";
      const summary = `Briefing Summary (Markdown):\n\n${result?.briefing || ''}`;
      const message = `${intro}\n\n${summary}`;

      // Persist original context invisibly as message metadata for backend-only prompt building
      const briefingContext = {
        gmail_digest: artifacts?.gmail_digest?.data ?? artifacts?.gmail_digest ?? {},
        calendar_events: artifacts?.calendar_events?.data ?? artifacts?.calendar_events ?? {},
        gchat_digest: artifacts?.gchat_digest?.data ?? artifacts?.gchat_digest ?? {},
        kb_insights: artifacts?.kb_insights?.data ?? artifacts?.kb_insights ?? {},
      };

      await chatAPI.addMessage(conv.id, {
        role: 'assistant',
        content: message,
        metadata: { morning_briefing: briefingContext }
      });
      navigate(`/chat?conversationId=${conv.id}`);
    } catch (e) {
      setError(e?.response?.data?.error?.message || e.message);
    } finally {
      setChatLoading(false);
    }
  };

  const artifacts = result?.artifacts || {};

  return (
    <Box>
      <Box display="flex" alignItems="center" justifyContent="space-between" mb={2}>
        <Typography variant="h4">Morning Briefing</Typography>
        <Box display="flex" alignItems="center" gap={2}>
          <FormControl size="small" sx={{ minWidth: 260 }}>
            <InputLabel id="briefing-model-config-label">Model Configuration</InputLabel>
            <Select
              labelId="briefing-model-config-label"
              id="briefing-model-config"
              value={modelConfigurationId}
              label="Model Configuration"
              onChange={(e) => setModelConfigurationId(e.target.value)}
            >
              {modelConfigs.map((c) => (
                <MenuItem key={c.id} value={c.id}>
                  {c.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <Button variant="contained" startIcon={<RunIcon />} onClick={runBriefing} disabled={loading}>
            {loading ? 'Running…' : 'Run Morning Briefing'}
          </Button>
          <Button
            variant="outlined"
            startIcon={<ChatIcon />}
            onClick={handleChatAboutSummary}
            disabled={chatLoading || !result || !modelConfigurationId}
          >
            {chatLoading ? 'Starting chat…' : 'Chat with this summary'}
          </Button>
        </Box>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>
      )}

      {loading && (
        <Box display="flex" justifyContent="center" alignItems="center" minHeight={200}>
          <CircularProgress />
        </Box>
      )}

      {!loading && result && (
        <>
          <Card sx={{ mb: 2 }}>
            <CardContent>
              <Typography variant="h6" gutterBottom>Briefing Summary</Typography>
              <Box sx={{
                '& h1, & h2, & h3, & h4': { marginTop: 2, marginBottom: 1 },
                '& p': { margin: '0.5rem 0' },
                '& ul, & ol': { paddingLeft: '1.25rem', marginTop: '0.5rem', marginBottom: '0.5rem' },
                '& pre': { backgroundColor: '#0f0f0f0a', padding: '8px', borderRadius: '4px', overflow: 'auto' },
                '& code': { backgroundColor: '#0f0f0f14', padding: '2px 4px', borderRadius: '4px' },
                '& table': { borderCollapse: 'collapse', width: '100%', margin: '8px 0' },
                '& th, & td': { border: '1px solid rgba(0,0,0,0.12)', padding: '6px 8px' }
              }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{result?.briefing || ''}</ReactMarkdown>
              </Box>
            </CardContent>
          </Card>

          <Grid container spacing={2}>
            <Grid item xs={12} md={3}>
              <Section
                title="Calendar"
                entry={artifacts?.calendar_events}
                onViewRaw={() => setViewer({ open: true, title: 'Calendar Raw JSON', data: artifacts?.calendar_events })}
              />
            </Grid>
            <Grid item xs={12} md={3}>
              <Section
                title="Gmail"
                entry={artifacts?.gmail_digest}
                onViewRaw={() => setViewer({ open: true, title: 'Gmail Raw JSON', data: artifacts?.gmail_digest })}
              />
            </Grid>
            <Grid item xs={12} md={3}>
              <GChatSection
                entry={artifacts?.gchat_digest}
                onViewRaw={() => setViewer({ open: true, title: 'Google Chat Raw JSON', data: artifacts?.gchat_digest })}
              />
            </Grid>
            <Grid item xs={12} md={3}>
              <Section
                title="Knowledge Base"
                entry={artifacts?.kb_insights}
                onViewRaw={() => setViewer({ open: true, title: 'Knowledge Base Raw JSON', data: artifacts?.kb_insights })}
              />
            </Grid>
          </Grid>
        </>
      )}

      <JsonViewer
        open={viewer.open}
        onClose={() => setViewer({ open: false, title: '', data: null })}
        title={viewer.title}
        data={viewer.data}
      />
    </Box>
  );
}
