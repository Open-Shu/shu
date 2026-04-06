import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { MemoryRouter } from 'react-router-dom';
import IntegrationsAdmin from '../IntegrationsAdmin';

vi.mock('../../services/mcpApi', () => ({
  mcpAPI: {
    listConnections: vi.fn(() =>
      Promise.resolve({
        data: {
          data: {
            items: [
              {
                id: 'mcp-1',
                name: 'Test MCP Server',
                url: 'https://mcp.example.com/sse',
                status: 'connected',
                enabled: true,
                tool_count: 3,
                discovered_tools: [],
                tool_configs: {},
              },
            ],
          },
        },
      })
    ),
    deleteConnection: vi.fn(),
    syncConnection: vi.fn(),
    updateConnection: vi.fn(),
    updateToolConfig: vi.fn(),
  },
}));

vi.mock('../../services/apiIntegrationsApi', () => ({
  apiIntegrationsAPI: {
    listConnections: vi.fn(() =>
      Promise.resolve({
        data: {
          data: {
            items: [
              {
                id: 'api-1',
                name: 'Test API Integration',
                base_url: 'https://api.example.com',
                status: 'connected',
                enabled: true,
                tool_count: 2,
                discovered_tools: [],
                tool_configs: {},
              },
            ],
          },
        },
      })
    ),
    deleteConnection: vi.fn(),
    syncConnection: vi.fn(),
    updateConnection: vi.fn(),
    updateToolConfig: vi.fn(),
  },
}));

vi.mock('../../services/api', () => ({
  extractDataFromResponse: (resp) => resp?.data?.data,
  formatError: (err) => err?.message || 'Unknown error',
}));

vi.mock('../PageHelpHeader', () => ({
  default: ({ title }) => <div data-testid="page-help-header">{title}</div>,
}));

vi.mock('../McpConnectionDialog', () => ({
  default: ({ open }) => (open ? <div data-testid="mcp-dialog">MCP Dialog</div> : null),
}));

vi.mock('../ApiIntegrationDialog', () => ({
  default: ({ open }) => (open ? <div data-testid="api-dialog">API Dialog</div> : null),
}));

vi.mock('../ToolConfigPanel', () => ({
  default: () => <div data-testid="tool-config-panel">Tools</div>,
}));

const renderWithProviders = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, cacheTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
};

describe('IntegrationsAdmin', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders both tabs', async () => {
    renderWithProviders(<IntegrationsAdmin />);
    expect(screen.getByText('MCP Servers')).toBeInTheDocument();
    expect(screen.getByText('API Integrations')).toBeInTheDocument();
  });

  it('renders the page header', async () => {
    renderWithProviders(<IntegrationsAdmin />);
    expect(screen.getByTestId('page-help-header')).toHaveTextContent('Integrations');
  });

  it('shows MCP tab content by default', async () => {
    renderWithProviders(<IntegrationsAdmin />);
    await waitFor(() => {
      expect(screen.getByText('Test MCP Server')).toBeInTheDocument();
    });
  });

  it('switches to API Integrations tab', async () => {
    renderWithProviders(<IntegrationsAdmin />);
    fireEvent.click(screen.getByText('API Integrations'));
    await waitFor(() => {
      expect(screen.getByText('Test API Integration')).toBeInTheDocument();
    });
  });

  it('shows Add Connection button on MCP tab', async () => {
    renderWithProviders(<IntegrationsAdmin />);
    await waitFor(() => {
      expect(screen.getByText('Add Connection')).toBeInTheDocument();
    });
  });

  it('shows Add Integration button on API tab', async () => {
    renderWithProviders(<IntegrationsAdmin />);
    fireEvent.click(screen.getByText('API Integrations'));
    await waitFor(() => {
      expect(screen.getByText('Add Integration')).toBeInTheDocument();
    });
  });
});
