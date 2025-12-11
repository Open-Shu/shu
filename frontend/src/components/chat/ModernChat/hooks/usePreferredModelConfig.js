import { useCallback, useMemo, useState } from 'react';

const STORAGE_KEY = 'shu.chat.preferredModelConfig';

const readStoredPreference = () => {
  if (typeof window === 'undefined') {
    return '';
  }
  try {
    return localStorage.getItem(STORAGE_KEY) || '';
  } catch (_) {
    return '';
  }
};

const persistPreference = (value) => {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    if (value) {
      localStorage.setItem(STORAGE_KEY, value);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch (_) {
    // Ignore storage failures
  }
};

export default function usePreferredModelConfig(availableModelConfigs, selectedConversation) {
  const [preferredModelConfig, setPreferredModelConfig] = useState(() => readStoredPreference());

  const availableIds = useMemo(
    () => new Set((availableModelConfigs || []).map((cfg) => cfg.id)),
    [availableModelConfigs]
  );

  const selectPreferredModelConfig = useCallback(
    (nextId) => {
      setPreferredModelConfig(nextId || '');
      persistPreference(nextId || '');
    },
    []
  );

  const resolveInitialModelConfig = useCallback(() => {
    const selectedId = selectedConversation?.model_configuration_id || '';
    if (selectedId && availableIds.has(selectedId)) {
      selectPreferredModelConfig(selectedId);
      return selectedId;
    }

    const storedPreference = preferredModelConfig;
    if (storedPreference && availableIds.has(storedPreference)) {
      return storedPreference;
    }

    const fallbackId = availableModelConfigs?.[0]?.id || '';
    if (fallbackId) {
      selectPreferredModelConfig(fallbackId);
    }
    return fallbackId;
  }, [
    availableIds,
    availableModelConfigs,
    preferredModelConfig,
    selectedConversation?.model_configuration_id,
    selectPreferredModelConfig,
  ]);

  return {
    preferredModelConfig,
    selectPreferredModelConfig,
    resolveInitialModelConfig,
  };
}
