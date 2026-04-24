import { render, screen, within, waitFor, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from 'react-query';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { vi } from 'vitest';
import LLMProviders from '../LLMProviders';
import * as api from '../../services/api';
import configService from '../../services/config';
import { useAuth } from '../../hooks/useAuth';

vi.mock('../../services/api', () => ({
  llmAPI: {
    getProviders: vi.fn(),
    getModels: vi.fn(),
    getProviderType: vi.fn(),
    getProviderTypes: vi.fn(),
    testProvider: vi.fn(),
    discoverModels: vi.fn(),
    syncModels: vi.fn(),
    createModel: vi.fn(),
    disableModel: vi.fn(),
    createProvider: vi.fn(),
    updateProvider: vi.fn(),
    deleteProvider: vi.fn(),
  },
  extractDataFromResponse: vi.fn((response) => response?.data),
  formatError: vi.fn((err) => ({ message: err?.message || 'error' })),
}));

vi.mock('../../services/config', () => ({
  default: { fetchConfig: vi.fn() },
}));

vi.mock('../../hooks/useAuth', () => ({
  useAuth: vi.fn(),
}));

const TOOLTIP_PROVIDER_MANAGED = 'This provider is managed by Shu and cannot be modified.';
const TOOLTIP_MODEL_MANAGED = 'This model is managed by Shu and cannot be modified.';
const LOCK_NOTICE = 'Provider creation is disabled on this deployment';

const PROVIDER_TYPES = [
  { key: 'openai', display_name: 'OpenAI', is_active: true },
  { key: 'anthropic', display_name: 'Anthropic', is_active: true },
];

const makeProvider = (overrides = {}) => ({
  id: 'p1',
  name: 'Managed OpenAI',
  provider_type: 'openai',
  api_endpoint: 'https://api.openai.com/v1',
  api_key: '',
  has_api_key: true,
  organization_id: '',
  is_active: true,
  provider_capabilities: {},
  rate_limit_rpm: 0,
  rate_limit_tpm: 0,
  budget_limit_monthly: null,
  endpoints: {},
  ...overrides,
});

const makeModel = (overrides = {}) => ({
  id: 'm1',
  provider_id: 'p1',
  model_name: 'gpt-4o',
  display_name: 'GPT-4o',
  model_type: 'chat',
  is_active: true,
  ...overrides,
});

const renderComponent = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, cacheTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  const theme = createTheme();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <LLMProviders />
      </ThemeProvider>
    </QueryClientProvider>
  );
  return { ...utils, queryClient };
};

const setupMocks = ({ providers, models = [], lockProviderCreations = false } = {}) => {
  useAuth.mockReturnValue({ canManageUsers: () => true });
  configService.fetchConfig.mockResolvedValue({ lock_provider_creations: lockProviderCreations });
  api.llmAPI.getProviders.mockResolvedValue({ data: providers });
  api.llmAPI.getModels.mockResolvedValue({ data: models });
  api.llmAPI.getProviderTypes.mockResolvedValue({ data: PROVIDER_TYPES });
  api.llmAPI.getProviderType.mockResolvedValue({
    data: { endpoints: {}, provider_capabilities: {}, base_url_template: '' },
  });
};

// Walk up from a text node to the enclosing Card root (MuiCard-root), so
// assertions scope cleanly to a single provider row rather than the whole page.
const getProviderCard = (name) => {
  const heading = screen.getByText(name);
  let node = heading;
  while (node && !(node.classList && node.classList.contains('MuiCard-root'))) {
    node = node.parentElement;
  }
  if (!node) {
    throw new Error(`Could not locate Card for provider "${name}"`);
  }
  return node;
};

describe('LLMProviders', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('system-managed provider rows', () => {
    it('renders the Managed-by-Shu badge on a system-managed provider row', async () => {
      setupMocks({ providers: [makeProvider({ is_system_managed: true })] });
      renderComponent();

      const card = await waitFor(() => getProviderCard('Managed OpenAI'));
      expect(within(card).getByLabelText('Managed by Shu')).toBeInTheDocument();
    });

    it('disables edit and delete icon buttons on a system-managed row', async () => {
      setupMocks({ providers: [makeProvider({ is_system_managed: true })] });
      renderComponent();

      const card = await waitFor(() => getProviderCard('Managed OpenAI'));
      expect(within(card).getByRole('button', { name: /Edit Provider/i })).toBeDisabled();
      expect(within(card).getByRole('button', { name: /Delete Provider/i })).toBeDisabled();
    });

    it('keeps the Manage Models affordance enabled on a system-managed row', async () => {
      setupMocks({ providers: [makeProvider({ is_system_managed: true })] });
      renderComponent();

      const card = await waitFor(() => getProviderCard('Managed OpenAI'));
      const manageButton = within(card).getByTitle('Manage Models');
      expect(manageButton).not.toBeDisabled();
    });

    it('exposes the verbatim provider-managed tooltip text on the badge', async () => {
      setupMocks({ providers: [makeProvider({ is_system_managed: true })] });
      renderComponent();

      const card = await waitFor(() => getProviderCard('Managed OpenAI'));
      fireEvent.mouseOver(within(card).getByLabelText('Managed by Shu'));
      await waitFor(() => {
        expect(screen.getByRole('tooltip')).toHaveTextContent(TOOLTIP_PROVIDER_MANAGED);
      });
    });

    it('exposes the verbatim provider-managed tooltip on the disabled Edit button wrapper', async () => {
      setupMocks({ providers: [makeProvider({ is_system_managed: true })] });
      renderComponent();

      const card = await waitFor(() => getProviderCard('Managed OpenAI'));
      // Disabled MUI buttons cannot fire events directly — the Tooltip wraps
      // them in a <span> that does. Hover the wrapper to surface the tooltip.
      const editBtn = within(card).getByRole('button', { name: /Edit Provider/i });
      fireEvent.mouseOver(editBtn.closest('span'));
      await waitFor(() => {
        expect(screen.getByRole('tooltip')).toHaveTextContent(TOOLTIP_PROVIDER_MANAGED);
      });
    });

    it('treats a missing is_system_managed field as false (no badge, controls enabled)', async () => {
      const provider = makeProvider();
      delete provider.is_system_managed;
      setupMocks({ providers: [provider] });
      renderComponent();

      const card = await waitFor(() => getProviderCard('Managed OpenAI'));
      expect(within(card).queryByLabelText('Managed by Shu')).not.toBeInTheDocument();
      expect(within(card).getByRole('button', { name: /Edit Provider/i })).not.toBeDisabled();
      expect(within(card).getByRole('button', { name: /Delete Provider/i })).not.toBeDisabled();
    });
  });

  describe('system-managed model chips', () => {
    it('renders the managed badge next to each model chip and disables onDelete', async () => {
      const provider = makeProvider({ is_system_managed: true });
      const model = makeModel({ provider_id: provider.id });
      setupMocks({ providers: [provider], models: [model] });
      renderComponent();

      const card = await waitFor(() => getProviderCard('Managed OpenAI'));
      fireEvent.click(within(card).getByTitle('Manage Models'));

      const dialog = await screen.findByRole('dialog', { name: /Manage Models/i });
      const chipLabel = within(dialog).getByText('GPT-4o');
      const chipRoot = chipLabel.closest('.MuiChip-root');
      expect(chipRoot).toHaveClass('Mui-disabled');
      // onDelete undefined → no delete icon rendered on the chip.
      expect(chipRoot.querySelector('.MuiChip-deleteIcon')).toBeNull();

      const badge = within(dialog).getAllByLabelText('Managed by Shu')[0];
      expect(badge).toBeInTheDocument();

      fireEvent.mouseOver(badge);
      await waitFor(() => {
        expect(screen.getByRole('tooltip')).toHaveTextContent(TOOLTIP_MODEL_MANAGED);
      });
    });

    it('keeps chip onDelete enabled when parent provider is NOT system-managed', async () => {
      const provider = makeProvider({ is_system_managed: false });
      const model = makeModel({ provider_id: provider.id });
      setupMocks({ providers: [provider], models: [model] });
      renderComponent();

      const card = await waitFor(() => getProviderCard('Managed OpenAI'));
      fireEvent.click(within(card).getByTitle('Manage Models'));
      const dialog = await screen.findByRole('dialog', { name: /Manage Models/i });

      const chipLabel = within(dialog).getByText('GPT-4o');
      const chipRoot = chipLabel.closest('.MuiChip-root');
      expect(chipRoot).not.toHaveClass('Mui-disabled');
      expect(chipRoot.querySelector('.MuiChip-deleteIcon')).not.toBeNull();
    });
  });

  describe('provider creation lock', () => {
    it('hides the Add Provider button when lock_provider_creations is TRUE', async () => {
      setupMocks({ providers: [makeProvider()], lockProviderCreations: true });
      renderComponent();

      await waitFor(() => expect(screen.getByText('Managed OpenAI')).toBeInTheDocument());
      await waitFor(() => {
        expect(screen.queryByRole('button', { name: /Add Provider/i })).not.toBeInTheDocument();
      });
    });

    it('shows the Add Provider button when lock_provider_creations is FALSE', async () => {
      setupMocks({ providers: [makeProvider()], lockProviderCreations: false });
      renderComponent();

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Add Provider/i })).toBeInTheDocument();
      });
    });

    it('hides the empty-state Add Provider CTA when lock_provider_creations is TRUE', async () => {
      setupMocks({ providers: [], lockProviderCreations: true });
      renderComponent();

      await waitFor(() => expect(screen.getByText(/No LLM Providers Configured/i)).toBeInTheDocument());
      expect(screen.queryByRole('button', { name: /Add Provider/i })).not.toBeInTheDocument();
    });

    it('opens the full create dialog (no lockout notice) when lock flag is FALSE', async () => {
      setupMocks({ providers: [makeProvider()], lockProviderCreations: false });
      renderComponent();

      const addBtn = await screen.findByRole('button', { name: /Add Provider/i });
      fireEvent.click(addBtn);

      const dialog = await screen.findByRole('dialog', { name: /Add LLM Provider/i });
      expect(within(dialog).getByLabelText(/Provider Name/i)).toBeInTheDocument();
      expect(within(dialog).queryByText(LOCK_NOTICE)).not.toBeInTheDocument();
    });

    it('renders the lockout notice when the create dialog is forced open while locked', async () => {
      // Start unlocked so the Add Provider button is clickable and opens the
      // dialog; then flip the public-config cache to locked=TRUE. The forced-
      // open branch swaps the form body for the lockout notice.
      setupMocks({ providers: [makeProvider()], lockProviderCreations: false });
      const { queryClient } = renderComponent();

      const addBtn = await screen.findByRole('button', { name: /Add Provider/i });
      fireEvent.click(addBtn);
      await screen.findByRole('dialog', { name: /Add LLM Provider/i });

      // Flip the flag via the query cache — no refetch needed; the component
      // re-renders on cache change via its useQuery subscription.
      queryClient.setQueryData('public-config', { lock_provider_creations: true });

      await waitFor(() => {
        expect(screen.getByText(LOCK_NOTICE)).toBeInTheDocument();
      });
    });
  });
});
