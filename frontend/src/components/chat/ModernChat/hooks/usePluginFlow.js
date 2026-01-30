import { useState, useMemo, useCallback } from "react";
import { useQuery } from "react-query";

import chatPluginsAPI from "../../../../services/chatPluginsApi";
import { extractDataFromResponse } from "../../../../services/api";

const usePluginFlow = ({ pluginsEnabled }) => {
  const chatPluginsQuery = useQuery(
    ["chat-plugins", "list"],
    () => chatPluginsAPI.list().then(extractDataFromResponse),
    { enabled: pluginsEnabled },
  );

  const chatPluginDescriptors = useMemo(() => {
    if (!pluginsEnabled) {
      return [];
    }
    const payload = chatPluginsQuery.data;
    if (payload && Array.isArray(payload.plugins)) {
      return payload.plugins;
    }
    return [];
  }, [pluginsEnabled, chatPluginsQuery.data]);

  const chatPluginsSummaryText = useMemo(() => {
    if (!chatPluginDescriptors.length) {
      return "";
    }
    return chatPluginDescriptors
      .map((entry) => entry.title || `${entry.name}:${entry.op}`)
      .join(", ");
  }, [chatPluginDescriptors]);

  const showPluginInfoBanner =
    process.env.NODE_ENV !== "production" &&
    pluginsEnabled &&
    Boolean(chatPluginsSummaryText);

  const [pluginPickerOpen, setPluginPickerOpen] = useState(false);
  const [pluginModalOpen, setPluginModalOpen] = useState(false);
  const [selectedPlugin, setSelectedPlugin] = useState(null);
  const [pluginRun, setPluginRun] = useState(null);

  const openPluginPicker = useCallback(() => {
    setPluginPickerOpen(true);
  }, []);

  const closePluginPicker = useCallback(() => {
    setPluginPickerOpen(false);
  }, []);

  const selectPlugin = useCallback((plugin) => {
    setSelectedPlugin(plugin);
    setPluginPickerOpen(false);
    setPluginModalOpen(true);
  }, []);

  const closePluginModal = useCallback(() => {
    setPluginModalOpen(false);
  }, []);

  return {
    chatPluginsSummaryText,
    showPluginInfoBanner,
    pluginPickerOpen,
    openPluginPicker,
    closePluginPicker,
    pluginModalOpen,
    closePluginModal,
    selectedPlugin,
    selectPlugin,
    pluginRun,
    setPluginRun,
  };
};

export default usePluginFlow;
