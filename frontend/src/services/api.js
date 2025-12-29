import axios from 'axios';
import { getApiV1Base } from './baseUrl';
import { log } from '../utils/log';


const DEFAULT_TIMEOUT_MS = Number.parseInt(process.env.REACT_APP_API_TIMEOUT_MS || '90000', 10);

const api = axios.create({
  baseURL: getApiV1Base(),
  timeout: Number.isFinite(DEFAULT_TIMEOUT_MS) ? DEFAULT_TIMEOUT_MS : 90000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor for authentication and logging
api.interceptors.request.use(
  (config) => {
    log.debug(`API Request: ${config.method?.toUpperCase()} ${config.url}`);

    // Add authentication header if token exists
    const token = localStorage.getItem('shu_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }

    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Flag to prevent multiple refresh attempts
let isRefreshing = false;
let failedQueue = [];

const processQueue = (error, token = null) => {
  failedQueue.forEach(prom => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token);
    }
  });

  failedQueue = [];
};

/**
 * Attempt to refresh the access token using the stored refresh token.
 * Returns the new tokens on success, null if no refresh token available.
 * Throws on refresh failure.
 */
const attemptTokenRefresh = async () => {
  const refreshToken = localStorage.getItem('shu_refresh_token');
  if (!refreshToken) {
    return null;
  }

  const response = await api.post('/auth/refresh', {
    refresh_token: refreshToken
  });

  const { access_token, refresh_token: newRefreshToken } = response.data.data;

  // Update stored tokens
  localStorage.setItem('shu_token', access_token);
  localStorage.setItem('shu_refresh_token', newRefreshToken);

  // Update default authorization header
  api.defaults.headers.common['Authorization'] = `Bearer ${access_token}`;

  return { access_token, refresh_token: newRefreshToken };
};

// Response interceptor for error handling and authentication
api.interceptors.response.use(
  (response) => {
    // Check if server indicates token needs refresh
    if (response.headers['x-token-refresh-needed'] === 'true') {
      // Trigger background token refresh
      refreshTokenInBackground();
    }

    // Don't automatically extract data - let components handle envelope format
    return response;
  },
  async (error) => {
    const originalRequest = error.config;

    log.error('API Error:', error.response?.data || error.message);

    // For the refresh endpoint itself, just propagate 401 to avoid infinite loops
    if (error.response?.status === 401) {
      const requestUrl = originalRequest?.url || '';
      if (requestUrl.includes('/auth/refresh')) {
        return Promise.reject(error);
      }
    }

    // Handle authentication errors (401 Unauthorized)
    if (error.response?.status === 401 && !originalRequest._retry) {
      if (isRefreshing) {
        // If we're already refreshing, queue this request
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        }).then(token => {
          if (token) {
            originalRequest.headers.Authorization = `Bearer ${token}`;
          }
          return api(originalRequest);
        }).catch(err => {
          return Promise.reject(err);
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        const tokens = await attemptTokenRefresh();

        if (tokens) {
          processQueue(null, tokens.access_token);

          // Retry the original request
          originalRequest.headers.Authorization = `Bearer ${tokens.access_token}`;
          return api(originalRequest);
        } else {
          // No refresh token available, clear everything and redirect
          localStorage.removeItem('shu_token');
          localStorage.removeItem('shu_refresh_token');

          if (!window.location.pathname.includes('/auth')) {
            window.location.href = '/auth';
          }

          return Promise.reject(new Error('Authentication required - redirecting to login'));
        }
      } catch (refreshError) {
        processQueue(refreshError, null);

        // Refresh failed, clear tokens and redirect
        localStorage.removeItem('shu_token');
        localStorage.removeItem('shu_refresh_token');

        if (!window.location.pathname.includes('/auth')) {
          window.location.href = '/auth';
        }

        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

// Background token refresh function
const refreshTokenInBackground = async () => {
  if (isRefreshing) return;

  const refreshToken = localStorage.getItem('shu_refresh_token');
  if (!refreshToken) return;

  try {
    isRefreshing = true;
    const response = await api.post('/auth/refresh', {
      refresh_token: refreshToken
    });

    const { access_token, refresh_token: newRefreshToken } = response.data.data;

    // Update stored tokens
    localStorage.setItem('shu_token', access_token);
    localStorage.setItem('shu_refresh_token', newRefreshToken);

    log.info('Token refreshed in background');
  } catch (error) {
    log.warn('Background token refresh failed:', error);
  } finally {
    isRefreshing = false;
  }
};

// Authentication endpoints
export const authAPI = {
  login: (googleToken) => api.post('/auth/login', { google_token: googleToken }),
  loginWithPassword: (email, password) => api.post('/auth/login/password', { email, password }),
  register: (userData) => api.post('/auth/register', userData),
  refresh: (refreshToken) => api.post('/auth/refresh', { refresh_token: refreshToken }),
  getCurrentUser: () => api.get('/auth/me'),
  getUsers: () => api.get('/auth/users'),
  createUser: (userData) => api.post('/auth/users', userData),
  updateUser: (userId, data) => api.put(`/auth/users/${userId}`, data),
  deleteUser: (userId) => api.delete(`/auth/users/${userId}`),
  activateUser: (userId) => api.patch(`/auth/users/${userId}/activate`),
  deactivateUser: (userId) => api.patch(`/auth/users/${userId}/deactivate`),

  exchangeGoogleLogin: (code) => api.post('/auth/google/exchange-login', { code }),
};

// Health endpoints
export const healthAPI = {
  getHealth: () => api.get('/health'),
  getReadiness: () => api.get('/health/readiness'),
  getLiveness: () => api.get('/health/liveness'),
  getDatabase: () => api.get('/health/database'),
};

// System endpoints
export const systemAPI = {
  getVersion: () => api.get('/system/version'),
};


// Branding endpoints
export const brandingAPI = {
  getBranding: () => api.get('/settings/branding'),
  updateBranding: (payload) => api.patch('/settings/branding', payload),
  uploadLogo: (file) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post('/settings/branding/logo', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  uploadFavicon: (file) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post('/settings/branding/favicon', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
};

// Knowledge base endpoints
export const knowledgeBaseAPI = {
  list: () => api.get('/knowledge-bases'),
  get: (id) => api.get(`/knowledge-bases/${id}`),
  create: (data) => api.post('/knowledge-bases', data),
  update: (id, data) => api.put(`/knowledge-bases/${id}`, data),
  delete: (id) => api.delete(`/knowledge-bases/${id}`),
  getDocuments: (id, params = {}) => api.get(`/knowledge-bases/${id}/documents`, { params }),
  getDocument: (id, docId) => api.get(`/knowledge-bases/${id}/documents/${docId}`),
  // Document upload
  uploadDocuments: (id, files, onUploadProgress) => {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));
    return api.post(`/knowledge-bases/${id}/documents/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress,
    });
  },
  // RAG Configuration endpoints
  getRAGConfig: (id) => api.get(`/knowledge-bases/${id}/rag-config`),
  updateRAGConfig: (id, config) => api.put(`/knowledge-bases/${id}/rag-config`, config),
  getRAGTemplates: () => api.get('/knowledge-bases/rag-config/templates'),
  // Permission management endpoints
  getPermissions: (id) => api.get(`/knowledge-bases/${id}/permissions`),
  grantPermission: (id, data) => api.post(`/knowledge-bases/${id}/permissions`, data),
  revokePermission: (id, permissionId) => api.delete(`/knowledge-bases/${id}/permissions/${permissionId}`),
  getEffectivePermissions: (id, userId = null) => api.get(`/knowledge-bases/${id}/effective-permissions`, { params: userId ? { user_id: userId } : {} }),
};



// Query endpoints
export const queryAPI = {
  search: (kbId, query) => api.post(`/query/${kbId}/search`, query),
  getStats: (kbId) => api.get(`/query/${kbId}/stats`),
};



// LLM endpoints
export const llmAPI = {
  // Provider management
  getProviders: () => api.get('/llm/providers'),
  getProvider: (id) => api.get(`/llm/providers/${id}`),
  createProvider: (data) => api.post('/llm/providers', data),
  updateProvider: (id, data) => api.put(`/llm/providers/${id}`, data),
  deleteProvider: (id) => api.delete(`/llm/providers/${id}`),
  testProvider: (id) => api.post(`/llm/providers/${id}/test`),

  // Provider type definitions (read-only)
  getProviderTypes: () => api.get('/llm/provider-types'),
  getProviderType: (key) => api.get(`/llm/provider-types/${encodeURIComponent(key)}`),

  // Model management
  getModels: (providerId = null) => api.get('/llm/models', { params: providerId ? { provider_id: providerId } : {} }),
  createModel: (providerId, data) => api.post(`/llm/providers/${providerId}/models`, data),

  // Model discovery
  discoverModels: (providerId) => api.get(`/llm/providers/${providerId}/discover-models`),
  syncModels: (providerId, selectedModels = null) => api.post(`/llm/providers/${providerId}/sync-models`, selectedModels),
  disableModel: (providerId, modelId) => api.delete(`/llm/providers/${providerId}/models/${modelId}`),

  // Health and monitoring
  getHealth: () => api.get('/llm/health'),
};

// Modern Chat API endpoints
export const chatAPI = {
  // Conversation management
  createConversation: (data) => api.post('/chat/conversations', data),
  createConversationWithModelConfig: (data) => api.post('/chat/conversations', data),
  listConversations: (params = {}) => api.get('/chat/conversations', { params }),
  getConversation: (id) => api.get(`/chat/conversations/${id}`),
  updateConversation: (id, data) => api.put(`/chat/conversations/${id}`, data),
  deleteConversation: (id) => api.delete(`/chat/conversations/${id}`),

  // Message management
  getMessages: (conversationId, params = {}) => api.get(`/chat/conversations/${conversationId}/messages`, { params }),
  addMessage: (conversationId, data) => api.post(`/chat/conversations/${conversationId}/messages`, data),

  // Core chat functionality
  sendMessage: (conversationId, data, config = {}) => api.post(`/chat/conversations/${conversationId}/send`, data, config),
  streamMessage: async (conversationId, data, options = {}) => {
    // Use POST /send with stream=true to support attachments
    // This function handles 401 errors by refreshing the token and retrying once
    const { signal, headers: extraHeaders } = options || {};

    const makeRequest = (authToken) => {
      const headers = { 'Accept': 'text/event-stream', 'Content-Type': 'application/json' };
      if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

      return fetch(`${api.defaults.baseURL}/chat/conversations/${conversationId}/send`, {
        method: 'POST',
        headers: { ...headers, ...(extraHeaders || {}) },
        body: JSON.stringify({ ...data }),
        signal,
      });
    };

    const token = localStorage.getItem('shu_token');
    const response = await makeRequest(token);

    // Handle 401 by refreshing token and retrying once
    if (response.status === 401) {
      try {
        log.debug('Streaming request got 401, attempting token refresh...');
        const tokens = await attemptTokenRefresh();

        if (tokens) {
          log.info('Token refreshed during streaming, retrying request...');
          return makeRequest(tokens.access_token);
        }
      } catch (refreshError) {
        log.warn('Token refresh failed during streaming:', refreshError);
        // Fall through to return original 401 response
      }
    }

    return response;
  },
  switchConversationModel: (conversationId, data) => api.post(`/chat/conversations/${conversationId}/switch-model`, data),

  // Attachments
  uploadAttachment: (conversationId, file) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post(`/chat/conversations/${conversationId}/attachments`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  viewAttachment: (attachmentId) => api.get(`/chat/attachments/${attachmentId}/view`, {
    responseType: 'blob',
  }),
};

// Model Configuration API endpoints
export const modelConfigAPI = {
  // Model configuration management
  list: (params = {}) => api.get('/model-configurations', { params }),
  get: (id) => api.get(`/model-configurations/${id}`),
  create: (data) => api.post('/model-configurations', data),
  update: (id, data) => api.put(`/model-configurations/${id}`, data),
  delete: (id) => api.delete(`/model-configurations/${id}`),

  // Testing
  test: (id, data) => api.post(`/model-configurations/${id}/test`, data),

  // KB Prompt Management
  getKBPrompts: (id) => api.get(`/model-configurations/${id}/kb-prompts`),
  assignKBPrompt: (id, data) => api.post(`/model-configurations/${id}/kb-prompts`, data),
  removeKBPrompt: (id, kbId) => api.delete(`/model-configurations/${id}/kb-prompts/${kbId}`),
};

// User Preferences API endpoints
export const userPreferencesAPI = {
  getPreferences: () => api.get('/user/preferences'),
  updatePreferences: (data) => api.put('/user/preferences', data),
  patchPreferences: (data) => api.patch('/user/preferences', data)
};

// Groups API endpoints
export const groupsAPI = {
  list: () => api.get('/groups'),
  get: (id) => api.get(`/groups/${id}`),
  create: (data) => api.post('/groups', data),
  update: (id, data) => api.put(`/groups/${id}`, data),
  delete: (id) => api.delete(`/groups/${id}`),
  getMembers: (id) => api.get(`/groups/${id}/members`),
  addMember: (id, userId) => api.post(`/groups/${id}/members`, { user_id: userId }),
  removeMember: (id, userId) => api.delete(`/groups/${id}/members/${userId}`),
};

// User Permissions API endpoints
export const userPermissionsAPI = {
  getCurrentUserKBPermissions: () => api.get('/users/me/permissions/knowledge-bases'),
  getCurrentUserGroups: () => api.get('/users/me/groups'),
  getUserKBPermissions: (userId) => api.get(`/users/${userId}/permissions/knowledge-bases`),
  getUserGroups: (userId) => api.get(`/users/${userId}/groups`),
};

// Side-calls API endpoints
export const sideCallsAPI = {
  generateSummary: (conversationId, data = {}) => api.post(`/side-calls/summary/${conversationId}`, data),
  autoRename: (conversationId, data = {}) => api.post(`/side-calls/auto-rename/${conversationId}`, data),
  unlockAutoRename: (conversationId) => api.post(`/side-calls/auto-rename/${conversationId}/unlock`),
  getConfig: () => api.get('/side-calls/config').then(extractDataFromResponse),
  setConfig: (data) => api.post('/side-calls/config', data).then(extractDataFromResponse),
};




// Host Auth endpoints (generic)
export const hostAuthAPI = {
  status: (providersCsv) => api.get('/host/auth/status', { params: providersCsv ? { providers: providersCsv } : {} }),
  authorize: (provider, scopesCsv) => api.get('/host/auth/authorize', { params: { provider, ...(scopesCsv ? { scopes: scopesCsv } : {}) } }),
  exchange: (payload) => api.post('/host/auth/exchange', payload),
  disconnect: (provider) => api.post('/host/auth/disconnect', { provider }),
  delegationCheck: (provider, subject, scopes) => api.post('/host/auth/delegation-check', { provider, subject, scopes }),
  serviceAccountCheck: (provider, scopes) => api.post('/host/auth/service-account-check', { provider, scopes }),
  // TASK-163: Subscriptions CRUD and consent-scopes (server union)
  consentScopes: (provider) => api.get('/host/auth/consent-scopes', { params: { provider } }),
  listSubscriptions: (provider, accountId) => api.get('/host/auth/subscriptions', { params: { provider, ...(accountId ? { account_id: accountId } : {}) } }),
  subscribe: (provider, pluginName, accountId) => api.post('/host/auth/subscriptions', { provider, plugin_name: pluginName, ...(accountId ? { account_id: accountId } : {}) }),
  unsubscribe: (provider, pluginName, accountId) => api.request({ method: 'DELETE', url: '/host/auth/subscriptions', data: { provider, plugin_name: pluginName, ...(accountId ? { account_id: accountId } : {}) } }),
};




// Utility functions
export const formatError = (error) => {
  // Handle envelope error format: { error: { message: "...", code: "...", details?: { provider_message?: string, ... } } }
  if (error.response?.data?.error) {
    const errorObj = error.response.data.error;
    // Prefer provider_message from structured details when available
    const rawProviderMsg = errorObj?.details?.provider_message || errorObj?.details?.message;
    const providerMsg = typeof rawProviderMsg === 'string' ? rawProviderMsg : (rawProviderMsg ? JSON.stringify(rawProviderMsg) : '');

    // message might be an object (e.g., { error: 'code', message: '...' })
    let baseMsg = '';
    const m = errorObj.message;
    if (typeof m === 'object' && m !== null) {
      const nestedCode = m.error || m.code || '';
      const nestedMsg = typeof m.message === 'string' ? m.message : (m.message ? JSON.stringify(m.message) : '');
      baseMsg = nestedCode ? `${nestedCode}${nestedMsg ? ': ' + nestedMsg : ''}` : (nestedMsg || JSON.stringify(m));
    } else {
      baseMsg = typeof m === 'string' ? m : (errorObj.code || 'Error');
    }

    const combined = providerMsg ? `${baseMsg ? baseMsg + ' — ' : ''}${providerMsg}` : baseMsg;
    return combined || JSON.stringify(errorObj);
  }

  // Handle Pydantic validation errors (422 status)
  if (error.response?.status === 422 && Array.isArray(error.response?.data?.detail)) {
    const validationErrors = error.response.data.detail;
    const errorMessages = validationErrors.map(err => {
      const location = err.loc ? err.loc.join('.') : 'unknown';
      return `${location}: ${err.msg}`;
    });
    return `Validation Error: ${errorMessages.join(', ')}`;
  }

  // Handle direct error format for non-envelope errors
  if (error.response?.data?.detail) {
    return error.response.data.detail;
  }

  return error.message || 'An unexpected error occurred';
};

// Utility function to extract data from envelope format
export const extractDataFromResponse = (response) => {
  // Handle double-wrapped envelope format: {data: {data: {...}}}
  if (response && typeof response === 'object' && 'data' in response) {
    const firstData = response.data;
    if (firstData && typeof firstData === 'object' && 'data' in firstData) {
      return firstData.data;
    }
    return firstData;
  }
  // Handle direct response format (fallback)
  return response;
};

// Utility function to extract items from paginated response
export const extractItemsFromResponse = (response) => {
  const data = extractDataFromResponse(response);

  // Handle paginated format: { items: [], total: 0, page: 1, ... }
  if (data && typeof data === 'object' && 'items' in data) {
    return data.items;
  }

  // Handle groups format: { groups: [], total_count: 0, page: 1, ... }
  if (data && typeof data === 'object' && 'groups' in data) {
    return data.groups;
  }

  // Handle knowledge bases format: { knowledge_bases: [], total: 0, ... }
  if (data && typeof data === 'object' && 'knowledge_bases' in data) {
    return data.knowledge_bases;
  }

  // Handle permissions format: { permissions: [], total: 0, ... }
  if (data && typeof data === 'object' && 'permissions' in data) {
    return data.permissions;
  }

  // Handle memberships format: { memberships: [], total: 0, ... }
  if (data && typeof data === 'object' && 'memberships' in data) {
    return data.memberships;
  }

  // Handle direct array format
  if (Array.isArray(data)) {
    return data;
  }
  return [];
};

// Utility function to get pagination info from response
export const extractPaginationFromResponse = (response) => {
  const data = extractDataFromResponse(response);
  if (data && typeof data === 'object' && 'total' in data) {
    return {
      total: data.total,
      page: data.page,
      size: data.size,
      pages: data.pages,
    };
  }
  return null;
};


// Chat regenerate endpoints
export const chatRegenerateAPI = {
  regenerate: (messageId, data = {}) => api.post(`/chat/messages/${messageId}/regenerate`, data),
  streamRegenerate: (messageId, data = {}, options = {}) => {
    const token = localStorage.getItem('shu_token');
    const url = `${api.defaults.baseURL}/chat/messages/${messageId}/regenerate`;
    const { signal, headers: extraHeaders } = options || {};

    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(extraHeaders || {}),
      },
      body: JSON.stringify({ ...data }),
      signal,
    });
  },
};

// Agents endpoints (Morning Briefing)
export const agentsAPI = {
  runMorningBriefing: (params = {}, timeoutMs = 120000) => api.post('/agents/morning-briefing/run', params, { timeout: timeoutMs }),
};

export default api;
