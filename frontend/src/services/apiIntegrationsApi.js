import api from './api';

const BASE = '/plugins/api/connections';

export const apiIntegrationsAPI = {
  listConnections: () => api.get(BASE),
  getConnection: (id) => api.get(`${BASE}/${encodeURIComponent(id)}`),
  createConnection: (yamlContent, authCredential) =>
    api.post(BASE, { yaml_content: yamlContent, auth_credential: authCredential || undefined }),
  updateConnection: (id, data) => api.patch(`${BASE}/${encodeURIComponent(id)}`, data),
  deleteConnection: (id) => api.delete(`${BASE}/${encodeURIComponent(id)}`),
  syncConnection: (id) => api.post(`${BASE}/${encodeURIComponent(id)}/sync`),
  updateToolConfig: (id, toolName, data) =>
    api.patch(`${BASE}/${encodeURIComponent(id)}/tools/${encodeURIComponent(toolName)}`, data),
};

export default apiIntegrationsAPI;
