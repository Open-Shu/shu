import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi } from 'vitest';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { QueryClient, QueryClientProvider } from 'react-query';
import KBPickerDialog from '../KBPickerDialog';

vi.mock('../../../../services/api', () => ({
  knowledgeBaseAPI: {
    list: vi.fn(),
  },
  extractItemsFromResponse: (res) => res ?? [],
  formatError: (err) => err?.message || 'Unknown error',
}));

const { knowledgeBaseAPI } = await import('../../../../services/api');

const mockKBs = [
  { id: 'kb-1', name: 'Alpha KB', description: 'First KB' },
  { id: 'kb-2', name: 'Beta KB', description: 'Second KB' },
  { id: 'kb-3', name: 'Gamma KB', description: null },
];

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, cacheTime: 0 } },
  });
  const theme = createTheme();
  function TestWrapper({ children }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ThemeProvider theme={theme}>{children}</ThemeProvider>
      </QueryClientProvider>
    );
  }
  return TestWrapper;
};

const renderDialog = (props = {}) => {
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    onSave: vi.fn(),
    selectedKBs: [],
    ...props,
  };
  return render(<KBPickerDialog {...defaultProps} />, { wrapper: createWrapper() });
};

describe('KBPickerDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    knowledgeBaseAPI.list.mockResolvedValue(mockKBs);
  });

  it('renders KB list with checkboxes', async () => {
    renderDialog();
    expect(await screen.findByText('Alpha KB')).toBeInTheDocument();
    expect(screen.getByText('Beta KB')).toBeInTheDocument();
    expect(screen.getByText('Gamma KB')).toBeInTheDocument();
  });

  it('shows descriptions when available', async () => {
    renderDialog();
    expect(await screen.findByText('First KB')).toBeInTheDocument();
    expect(screen.getByText('Second KB')).toBeInTheDocument();
  });

  it('pre-checks selected KBs', async () => {
    renderDialog({ selectedKBs: [{ id: 'kb-2', name: 'Beta KB' }] });
    await screen.findByText('Alpha KB');

    const checkboxes = screen.getAllByRole('checkbox');
    const kb2Checkbox = checkboxes[1];
    expect(kb2Checkbox).toBeChecked();
  });

  it('filters KBs by name', async () => {
    renderDialog();
    await screen.findByText('Alpha KB');

    const filterInput = screen.getByPlaceholderText('Filter by name...');
    fireEvent.change(filterInput, { target: { value: 'beta' } });

    expect(screen.queryByText('Alpha KB')).not.toBeInTheDocument();
    expect(screen.getByText('Beta KB')).toBeInTheDocument();
    expect(screen.queryByText('Gamma KB')).not.toBeInTheDocument();
  });

  it('shows empty filter message', async () => {
    renderDialog();
    await screen.findByText('Alpha KB');

    const filterInput = screen.getByPlaceholderText('Filter by name...');
    fireEvent.change(filterInput, { target: { value: 'nonexistent' } });

    expect(screen.getByText('No knowledge bases match your filter.')).toBeInTheDocument();
  });

  it('apply sends selected KBs as {id, name} objects', async () => {
    const onSave = vi.fn();
    renderDialog({ onSave });
    await screen.findByText('Alpha KB');

    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[2]);

    fireEvent.click(screen.getByText('Apply'));

    expect(onSave).toHaveBeenCalledWith([
      { id: 'kb-1', name: 'Alpha KB' },
      { id: 'kb-3', name: 'Gamma KB' },
    ]);
  });

  it('cancel calls onClose without saving', async () => {
    const onClose = vi.fn();
    const onSave = vi.fn();
    renderDialog({ onClose, onSave });
    await screen.findByText('Alpha KB');

    fireEvent.click(screen.getByText('Cancel'));

    expect(onClose).toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('clear deselects all checkboxes', async () => {
    renderDialog({ selectedKBs: [{ id: 'kb-1', name: 'Alpha KB' }] });
    await screen.findByText('Alpha KB');

    fireEvent.click(screen.getByText('Clear'));

    const checkboxes = screen.getAllByRole('checkbox');
    checkboxes.forEach((cb) => expect(cb).not.toBeChecked());
  });

  it('shows empty state when no KBs available', async () => {
    knowledgeBaseAPI.list.mockResolvedValue([]);
    renderDialog();

    expect(await screen.findByText('No knowledge bases available.')).toBeInTheDocument();
  });

  it('shows error state on fetch failure', async () => {
    knowledgeBaseAPI.list.mockRejectedValue(new Error('Network error'));
    renderDialog();

    expect(await screen.findByText('Network error')).toBeInTheDocument();
  });
});
