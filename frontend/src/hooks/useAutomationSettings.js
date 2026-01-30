import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "shu.automation.settings";

const DEFAULT_SETTINGS = {
  firstUserRename: true,
  firstAssistantSummary: true,
  cadenceInterval: 4,
};

const useAutomationSettings = () => {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return;
      }
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        setSettings((prev) => ({ ...prev, ...parsed }));
      }
    } catch (_) {
      // ignore corrupted storage; defaults already applied
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch (_) {
      // best-effort persistence only
    }
  }, [settings]);

  const updateSettings = useCallback((updates) => {
    setSettings((prev) => ({ ...prev, ...updates }));
  }, []);

  return [settings, updateSettings];
};

export default useAutomationSettings;
