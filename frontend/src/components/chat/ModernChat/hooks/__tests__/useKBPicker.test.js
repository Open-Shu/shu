import { renderHook, act } from '@testing-library/react';
import useKBPicker from '../useKBPicker';

describe('useKBPicker', () => {
  it('starts with empty selection', () => {
    const { result } = renderHook(() => useKBPicker());
    expect(result.current.selectedKBs).toEqual([]);
    expect(result.current.selectedKBIds).toEqual([]);
    expect(result.current.isKBPickerActive).toBe(false);
    expect(result.current.kbPickerLabel).toBe('');
    expect(result.current.kbPickerDialogOpen).toBe(false);
  });

  it('applyKBSelection sets selection and closes dialog', () => {
    const { result } = renderHook(() => useKBPicker());

    act(() => result.current.openKBPickerDialog());
    expect(result.current.kbPickerDialogOpen).toBe(true);

    act(() => {
      result.current.applyKBSelection([
        { id: 'kb-1', name: 'Alpha' },
        { id: 'kb-2', name: 'Beta' },
      ]);
    });

    expect(result.current.selectedKBs).toEqual([
      { id: 'kb-1', name: 'Alpha' },
      { id: 'kb-2', name: 'Beta' },
    ]);
    expect(result.current.selectedKBIds).toEqual(['kb-1', 'kb-2']);
    expect(result.current.isKBPickerActive).toBe(true);
    expect(result.current.kbPickerDialogOpen).toBe(false);
  });

  it('removeKB removes a single KB by id', () => {
    const { result } = renderHook(() => useKBPicker());

    act(() => {
      result.current.applyKBSelection([
        { id: 'kb-1', name: 'Alpha' },
        { id: 'kb-2', name: 'Beta' },
      ]);
    });

    act(() => result.current.removeKB('kb-1'));

    expect(result.current.selectedKBs).toEqual([{ id: 'kb-2', name: 'Beta' }]);
    expect(result.current.selectedKBIds).toEqual(['kb-2']);
  });

  it('clearKBSelection removes all KBs', () => {
    const { result } = renderHook(() => useKBPicker());

    act(() => {
      result.current.applyKBSelection([{ id: 'kb-1', name: 'Alpha' }]);
    });
    expect(result.current.isKBPickerActive).toBe(true);

    act(() => result.current.clearKBSelection());

    expect(result.current.selectedKBs).toEqual([]);
    expect(result.current.isKBPickerActive).toBe(false);
    expect(result.current.kbPickerLabel).toBe('');
  });

  it('computes label for 1-2 KBs', () => {
    const { result } = renderHook(() => useKBPicker());

    act(() => {
      result.current.applyKBSelection([{ id: 'kb-1', name: 'Alpha' }]);
    });
    expect(result.current.kbPickerLabel).toBe('KB: Alpha');

    act(() => {
      result.current.applyKBSelection([
        { id: 'kb-1', name: 'Alpha' },
        { id: 'kb-2', name: 'Beta' },
      ]);
    });
    expect(result.current.kbPickerLabel).toBe('KB: Alpha, Beta');
  });

  it('truncates label for 3+ KBs', () => {
    const { result } = renderHook(() => useKBPicker());

    act(() => {
      result.current.applyKBSelection([
        { id: 'kb-1', name: 'Alpha' },
        { id: 'kb-2', name: 'Beta' },
        { id: 'kb-3', name: 'Gamma' },
      ]);
    });
    expect(result.current.kbPickerLabel).toBe('KB: Alpha, Beta + 1 more');
  });

  it('open/close dialog toggles state', () => {
    const { result } = renderHook(() => useKBPicker());

    act(() => result.current.openKBPickerDialog());
    expect(result.current.kbPickerDialogOpen).toBe(true);

    act(() => result.current.closeKBPickerDialog());
    expect(result.current.kbPickerDialogOpen).toBe(false);
  });
});
