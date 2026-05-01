import { useCallback, useMemo, useState } from 'react';

const useKBPicker = () => {
  const [selectedKBs, setSelectedKBs] = useState([]);
  const [kbPickerDialogOpen, setKBPickerDialogOpen] = useState(false);

  const selectedKBIds = useMemo(() => selectedKBs.map((kb) => kb.id), [selectedKBs]);
  const isKBPickerActive = selectedKBs.length > 0;

  const kbPickerLabel = useMemo(() => {
    if (!isKBPickerActive) {
      return '';
    }
    const names = selectedKBs.map((kb) => kb.name);
    if (names.length <= 2) {
      return `KB: ${names.join(', ')}`;
    }
    return `KB: ${names.slice(0, 2).join(', ')} + ${names.length - 2} more`;
  }, [isKBPickerActive, selectedKBs]);

  const openKBPickerDialog = useCallback(() => setKBPickerDialogOpen(true), []);
  const closeKBPickerDialog = useCallback(() => setKBPickerDialogOpen(false), []);

  const applyKBSelection = useCallback((kbs) => {
    setSelectedKBs(kbs);
    setKBPickerDialogOpen(false);
  }, []);

  const clearKBSelection = useCallback(() => {
    setSelectedKBs([]);
  }, []);

  const removeKB = useCallback((kbId) => {
    setSelectedKBs((prev) => prev.filter((kb) => kb.id !== kbId));
  }, []);

  // Add a KB to the current selection if not already attached.
  // Used by Personal Knowledge auto-attach so additive flows don't trample
  // existing user choices.
  const ensureKBAttached = useCallback((kb) => {
    if (!kb || !kb.id) {
      return;
    }
    setSelectedKBs((prev) => {
      if (prev.some((existing) => existing.id === kb.id)) {
        return prev;
      }
      return [...prev, kb];
    });
  }, []);

  return {
    selectedKBs,
    selectedKBIds,
    isKBPickerActive,
    kbPickerLabel,
    kbPickerDialogOpen,
    openKBPickerDialog,
    closeKBPickerDialog,
    applyKBSelection,
    clearKBSelection,
    removeKB,
    ensureKBAttached,
  };
};

export default useKBPicker;
