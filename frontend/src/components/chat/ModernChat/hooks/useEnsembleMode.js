import { useCallback, useEffect, useMemo, useState } from 'react';

const sanitizeSelection = (ids, availableModelConfigs) => {
  if (!Array.isArray(ids) || ids.length === 0) {
    return [];
  }
  const validIds = new Set((availableModelConfigs || []).map((config) => config.id));
  const unique = [];
  for (const id of ids) {
    if (!id || !validIds.has(id)) {
      continue;
    }
    if (!unique.includes(id)) {
      unique.push(id);
    }
  }
  return unique;
};

const useEnsembleMode = (availableModelConfigs = []) => {
  const [selectedIds, setSelectedIds] = useState([]);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    setSelectedIds((prev) => {
      const sanitized = sanitizeSelection(prev, availableModelConfigs);
      if (sanitized.length === prev.length && sanitized.every((id, idx) => id === prev[idx])) {
        return prev;
      }
      return sanitized;
    });
  }, [availableModelConfigs]);

  const canConfigureEnsemble = useMemo(() => (availableModelConfigs || []).length > 1, [availableModelConfigs]);
  const isEnsembleModeActive = selectedIds.length > 0;

  const ensembleModeLabel = useMemo(() => {
    if (!isEnsembleModeActive) {
      return '';
    }
    const nameMap = selectedIds
      .map((id) => (availableModelConfigs || []).find((config) => config.id === id)?.name)
      .filter(Boolean);
    if (nameMap.length === 0) {
      return `Ensemble mode (${selectedIds.length})`;
    }
    if (nameMap.length <= 2) {
      return `Ensemble mode: ${nameMap.join(', ')}`;
    }
    return `Ensemble mode: ${nameMap.slice(0, 2).join(', ')} + ${nameMap.length - 2} more`;
  }, [availableModelConfigs, isEnsembleModeActive, selectedIds]);

  const openEnsembleDialog = useCallback(() => setDialogOpen(true), []);
  const closeEnsembleDialog = useCallback(() => setDialogOpen(false), []);

  const applyEnsembleSelection = useCallback(
    (ids) => {
      const sanitized = sanitizeSelection(ids, availableModelConfigs);
      setSelectedIds(sanitized);
      setDialogOpen(false);
    },
    [availableModelConfigs]
  );

  const clearEnsembleSelection = useCallback(() => {
    setSelectedIds([]);
  }, []);

  return {
    ensembleModeConfigIds: selectedIds,
    isEnsembleModeActive,
    ensembleModeLabel,
    ensembleDialogOpen: dialogOpen,
    openEnsembleDialog,
    closeEnsembleDialog,
    applyEnsembleSelection,
    clearEnsembleSelection,
    canConfigureEnsemble,
  };
};

export default useEnsembleMode;
