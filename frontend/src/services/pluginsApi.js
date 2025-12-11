import api from './api';

export const pluginsAPI = {
  list: () => api.get('/plugins'),
  get: (name) => api.get(`/plugins/${encodeURIComponent(name)}`),
  execute: (name, params = {}, agentKey = null) => api.post(`/plugins/${encodeURIComponent(name)}/execute`, {
    params,
    agent_key: agentKey,
  }),
  setEnabled: (name, enabled) => api.patch(`/plugins/admin/${encodeURIComponent(name)}/enable`, { enabled }),
  setSchema: (name, input_schema = null, output_schema = null) => api.put(`/plugins/admin/${encodeURIComponent(name)}/schema`, {
    input_schema,
    output_schema,
  }),
  sync: () => api.post('/plugins/admin/sync'),
  upload: (file, force=false) => {
    const form = new FormData();
    form.append('file', file);
    form.append('force', force ? 'true' : 'false');
    return api.post('/plugins/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } });
  },
  deletePlugin: (name) => api.delete(`/plugins/admin/${encodeURIComponent(name)}`),
  // Limits & quotas
  getLimits: (name) => api.get(`/plugins/admin/${encodeURIComponent(name)}/limits`),
  setLimits: (name, payload) => api.put(`/plugins/admin/${encodeURIComponent(name)}/limits`, payload),
  getLimitsStats: (prefix = 'rl:plugin:', limit = 50) => api.get('/plugins/admin/limits/stats', { params: { prefix, limit } }),
  // Secrets - Admin (per user+plugin, with scope support)
  listSecrets: (name, userId, scope = 'user') => api.get(`/plugins/admin/${encodeURIComponent(name)}/secrets`, { params: { user_id: userId, scope } }),
  setSecret: (name, key, userId, value, scope = 'user') => api.put(`/plugins/admin/${encodeURIComponent(name)}/secrets/${encodeURIComponent(key)}`, { user_id: userId, value, scope }),
  deleteSecret: (name, key, userId, scope = 'user') => api.delete(`/plugins/admin/${encodeURIComponent(name)}/secrets/${encodeURIComponent(key)}`, { params: { user_id: userId, scope } }),
  // Secrets - Self (current user's secrets, user-scoped only)
  listSelfSecrets: (name) => api.get(`/plugins/self/${encodeURIComponent(name)}/secrets`),
  setSelfSecret: (name, key, value) => api.put(`/plugins/self/${encodeURIComponent(name)}/secrets/${encodeURIComponent(key)}`, { value }),
  deleteSelfSecret: (name, key) => api.delete(`/plugins/self/${encodeURIComponent(name)}/secrets/${encodeURIComponent(key)}`),
};

export default pluginsAPI;

