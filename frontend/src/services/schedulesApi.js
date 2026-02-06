import api from './api';

export const schedulesAPI = {
  // Plugin Feeds
  list: (params = {}) => api.get('/plugins/admin/feeds', { params }),
  get: (id) => api.get(`/plugins/admin/feeds/${encodeURIComponent(id)}`),
  create: (payload) => api.post('/plugins/admin/feeds', payload),
  update: (id, payload) => api.patch(`/plugins/admin/feeds/${encodeURIComponent(id)}`, payload),
  delete: (id) => api.delete(`/plugins/admin/feeds/${encodeURIComponent(id)}`),
  runDue: () => api.post('/plugins/admin/feeds/run-due'),
  runNow: (id) => api.post(`/plugins/admin/feeds/${encodeURIComponent(id)}/run-now`),

  // Executions (admin)
  listExecutions: (params = {}) => api.get('/plugins/admin/executions', { params }),
  runPending: (opts = { limit: 10 }) => api.post('/plugins/admin/executions/run-pending', opts),
};

export default schedulesAPI;
