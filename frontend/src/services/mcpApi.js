import api from './api';

const BASE = '/plugins/mcp/connections';

export const mcpAPI = {
  listConnections: () => api.get(BASE),
  getConnection: (id) => api.get(`${BASE}/${encodeURIComponent(id)}`),
  createConnection: (data) => api.post(BASE, data),
  updateConnection: (id, data) => api.patch(`${BASE}/${encodeURIComponent(id)}`, data),
  deleteConnection: (id) => api.delete(`${BASE}/${encodeURIComponent(id)}`),
  syncConnection: (id) => api.post(`${BASE}/${encodeURIComponent(id)}/sync`),
  updateToolConfig: (id, toolName, data) =>
    api.patch(`${BASE}/${encodeURIComponent(id)}/tools/${encodeURIComponent(toolName)}`, data),
};

export default mcpAPI;
