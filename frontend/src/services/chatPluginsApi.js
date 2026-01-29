import api from "./api";

export const chatPluginsAPI = {
  list: () => api.get("/chat/plugins"),
};

export default chatPluginsAPI;
