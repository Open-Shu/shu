import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from 'react-query';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import LLMTester from '../LLMTester';
import * as api from '../../services/api';

// Mock dependencies
jest.mock('../../services/api');

// Polyfill TextEncoder for tests
if (typeof global.TextEncoder === 'undefined') {
  global.TextEncoder = require('util').TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
  global.TextDecoder = require('util').TextDecoder;
}

// Test wrapper component
const TestWrapper = ({ children }) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        cacheTime: 0,
      },
    },
  });
  const theme = createTheme();
  
  return (
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider theme={theme}>
          {children}
        </ThemeProvider>
      </QueryClientProvider>
    </BrowserRouter>
  );
};

/**
 * Unit Tests for Property 5: LLM Tester Pre-population
 * 
 * Feature: open-source-fixes, Property 5: LLM Tester Pre-population
 * Validates: Requirements 3.1, 3.2
 * 
 * Property: For any model configuration passed to the LLM Tester,
 * all configuration fields (provider, model, prompts, knowledge bases)
 * should be pre-populated in the tester.
 */
describe('LLMTester - Property 5: Pre-population', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    
    // Mock extractDataFromResponse and extractItemsFromResponse
    api.extractDataFromResponse = jest.fn().mockImplementation((response) => response?.data);
    api.extractItemsFromResponse = jest.fn().mockImplementation((response) => response?.data || []);
    
    // Mock getRAGConfig to prevent errors in QueryConfiguration component
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
      getRAGConfig: jest.fn().mockResolvedValue({ data: {} }),
    };
  });

  test('provider and model are displayed when configuration is pre-populated', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-123',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-123',
      name: 'Test Configuration',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component with pre-populated config
    render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load and pre-populate
    await waitFor(() => {
      expect(screen.getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Verify: Configuration is selected (check that the config name appears in the select)
    await waitFor(() => {
      const configName = screen.getByText(testConfig.name);
      expect(configName).toBeInTheDocument();
    });

    // Verify: Provider is displayed in configuration details
    await waitFor(() => {
      const providerChip = screen.getByText(/Provider: OpenAI/i);
      expect(providerChip).toBeInTheDocument();
    });

    // Verify: Model is displayed in configuration details
    await waitFor(() => {
      const modelChip = screen.getByText(/Model: gpt-4/i);
      expect(modelChip).toBeInTheDocument();
    });
  });

  test('model prompt is displayed when present in configuration', async () => {
    // Setup: Create test data with prompt
    const testProvider = {
      id: 'provider-456',
      name: 'Anthropic',
      provider_type: 'anthropic',
    };

    const testPrompt = {
      id: 'prompt-789',
      name: 'System Prompt',
      content: 'You are a helpful assistant.',
      entity_type: 'model',
    };

    const testConfig = {
      id: 'config-456',
      name: 'Config with Prompt',
      llm_provider_id: testProvider.id,
      model_name: 'claude-3-opus',
      prompt_id: testPrompt.id,
      prompt: testPrompt,
      is_active: true,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component with pre-populated config
    render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(screen.getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Verify: Model prompt section is displayed
    await waitFor(() => {
      const promptSection = screen.getByText('Model Prompt');
      expect(promptSection).toBeInTheDocument();
    });

    // Verify: Prompt name is displayed
    await waitFor(() => {
      const promptChip = screen.getByText(testPrompt.name);
      expect(promptChip).toBeInTheDocument();
    });
  });

  test('knowledge bases are displayed when present in configuration', async () => {
    // Setup: Create test data with knowledge bases
    const testProvider = {
      id: 'provider-789',
      name: 'Ollama',
      provider_type: 'ollama',
    };

    const testKB1 = {
      id: 'kb-001',
      name: 'Documentation KB',
      description: 'Product documentation',
    };

    const testKB2 = {
      id: 'kb-002',
      name: 'Support KB',
      description: 'Support articles',
    };

    const testConfig = {
      id: 'config-789',
      name: 'Config with KBs',
      llm_provider_id: testProvider.id,
      model_name: 'llama2',
      knowledge_base_ids: [testKB1.id, testKB2.id],
      knowledge_bases: [testKB1, testKB2],
      is_active: true,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [testKB1, testKB2] });

    // Render component with pre-populated config
    render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(screen.getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Verify: Knowledge Bases section is displayed
    await waitFor(() => {
      const kbSection = screen.getByText('Knowledge Bases');
      expect(kbSection).toBeInTheDocument();
    });

    // Verify: Each knowledge base is displayed
    await waitFor(() => {
      const kb1Chip = screen.getByText(testKB1.name);
      expect(kb1Chip).toBeInTheDocument();
      
      const kb2Chip = screen.getByText(testKB2.name);
      expect(kb2Chip).toBeInTheDocument();
    });
  });

  test('KB prompts are displayed when present in configuration', async () => {
    // Setup: Create test data with KB prompt assignments
    const testProvider = {
      id: 'provider-101',
      name: 'LM Studio',
      provider_type: 'lm_studio',
    };

    const testKB = {
      id: 'kb-101',
      name: 'Legal KB',
      description: 'Legal documents',
    };

    const testKBPrompt = {
      id: 'kb-prompt-101',
      name: 'Legal Context Prompt',
      content: 'Use legal terminology.',
      entity_type: 'kb',
    };

    const testKBPromptAssignment = {
      id: 'assignment-101',
      knowledge_base_id: testKB.id,
      prompt_id: testKBPrompt.id,
      prompt: testKBPrompt,
      knowledge_base: testKB,
    };

    const testConfig = {
      id: 'config-101',
      name: 'Config with KB Prompts',
      llm_provider_id: testProvider.id,
      model_name: 'mistral-7b',
      knowledge_base_ids: [testKB.id],
      knowledge_bases: [testKB],
      kb_prompt_assignments: [testKBPromptAssignment],
      is_active: true,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [testKB] });

    // Render component with pre-populated config
    render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(screen.getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Verify: KB Prompts label is displayed
    await waitFor(() => {
      const kbPromptsLabel = screen.getByText('KB Prompts:');
      expect(kbPromptsLabel).toBeInTheDocument();
    });

    // Verify: KB prompt name is displayed
    await waitFor(() => {
      const kbPromptChip = screen.getByText(testKBPrompt.name);
      expect(kbPromptChip).toBeInTheDocument();
    });
  });

  test('configuration selector is disabled when prePopulatedConfigId is provided', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-202',
      name: 'Azure OpenAI',
      provider_type: 'azure_openai',
    };

    const testConfig = {
      id: 'config-202',
      name: 'Azure Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4-turbo',
      is_active: true,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component with pre-populated config
    render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(screen.getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Verify: Configuration selector is disabled
    const configSelect = screen.getByLabelText('Model Configuration');
    await waitFor(() => {
      // The select should be disabled when prePopulatedConfigId is provided
      // Check if the parent div has the disabled class
      const selectParent = configSelect.closest('.MuiInputBase-root');
      expect(selectParent).toHaveClass('Mui-disabled');
    });
  });

  test('all fields are pre-populated for complete configuration', async () => {
    // Setup: Create complete test data
    const testProvider = {
      id: 'provider-999',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testPrompt = {
      id: 'prompt-999',
      name: 'Complete Prompt',
      content: 'You are an expert assistant.',
      entity_type: 'model',
    };

    const testKB = {
      id: 'kb-999',
      name: 'Complete KB',
      description: 'All the knowledge',
    };

    const testKBPrompt = {
      id: 'kb-prompt-999',
      name: 'KB Context',
      content: 'Use this context.',
      entity_type: 'kb',
    };

    const testKBPromptAssignment = {
      id: 'assignment-999',
      knowledge_base_id: testKB.id,
      prompt_id: testKBPrompt.id,
      prompt: testKBPrompt,
      knowledge_base: testKB,
    };

    const testConfig = {
      id: 'config-999',
      name: 'Complete Configuration',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4-complete',
      prompt_id: testPrompt.id,
      prompt: testPrompt,
      knowledge_base_ids: [testKB.id],
      knowledge_bases: [testKB],
      kb_prompt_assignments: [testKBPromptAssignment],
      is_active: true,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [testKB] });

    // Render component with pre-populated config
    render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(screen.getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Verify: All fields are present
    // 1. Provider
    await waitFor(() => {
      const providerChip = screen.getByText(/Provider: OpenAI/i);
      expect(providerChip).toBeInTheDocument();
    });

    // 2. Model
    await waitFor(() => {
      const modelChip = screen.getByText(/Model: gpt-4-complete/i);
      expect(modelChip).toBeInTheDocument();
    });

    // 3. Model Prompt
    await waitFor(() => {
      const promptSection = screen.getByText('Model Prompt');
      expect(promptSection).toBeInTheDocument();
      const promptChip = screen.getByText(testPrompt.name);
      expect(promptChip).toBeInTheDocument();
    });

    // 4. Knowledge Bases
    await waitFor(() => {
      const kbSection = screen.getByText('Knowledge Bases');
      expect(kbSection).toBeInTheDocument();
      const kbChip = screen.getByText(testKB.name);
      expect(kbChip).toBeInTheDocument();
    });

    // 5. KB Prompts
    await waitFor(() => {
      const kbPromptsLabel = screen.getByText('KB Prompts:');
      expect(kbPromptsLabel).toBeInTheDocument();
      const kbPromptChip = screen.getByText(testKBPrompt.name);
      expect(kbPromptChip).toBeInTheDocument();
    });
  });

  test('configuration selector is enabled when prePopulatedConfigId is not provided', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-303',
      name: 'Ollama',
      provider_type: 'ollama',
    };

    const testConfig = {
      id: 'config-303',
      name: 'Standalone Config',
      llm_provider_id: testProvider.id,
      model_name: 'llama2',
      is_active: true,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component WITHOUT pre-populated config
    render(
      <TestWrapper>
        <LLMTester />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(screen.getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Verify: Configuration selector is NOT disabled
    const configSelect = screen.getByLabelText('Model Configuration');
    await waitFor(() => {
      const selectParent = configSelect.closest('.MuiInputBase-root');
      expect(selectParent).not.toHaveClass('Mui-disabled');
    });
  });
});

/**
 * Unit Tests for Property 14: LLM Tester Resource Cleanup
 * 
 * Feature: open-source-fixes, Property 14: LLM Tester Resource Cleanup
 * Validates: Requirements 8.1, 8.2
 * 
 * Property: For any LLM Tester test execution, the test endpoint is called
 * and resources are managed by the backend (no client-side cleanup needed).
 * 
 * Note: The LLM Tester now uses the dedicated test endpoint (modelConfigAPI.test)
 * which handles resource management server-side. These tests verify the test
 * endpoint is called correctly and results are displayed properly.
 */
describe('LLMTester - Property 14: Resource Cleanup', () => {
  // Increase timeout for this suite due to multiple async operations
  jest.setTimeout(15000);

  beforeEach(() => {
    jest.clearAllMocks();
    
    // Mock extractDataFromResponse and extractItemsFromResponse
    api.extractDataFromResponse = jest.fn().mockImplementation((response) => response?.data);
    api.extractItemsFromResponse = jest.fn().mockImplementation((response) => response?.data || []);
    
    // Mock formatError
    api.formatError = jest.fn().mockImplementation((error) => error?.message || 'Unknown error');
  });

  test('test endpoint is called with correct parameters on successful test', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-cleanup-1',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-cleanup-1',
      name: 'Cleanup Test Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    const testResult = {
      success: true,
      response: 'Test response from LLM',
      model_used: 'gpt-4',
      token_usage: { total_tokens: 100 },
      response_time_ms: 500,
      prompt_applied: false,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
      testWithFile: jest.fn().mockResolvedValue({ data: testResult }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component
    const { getByText, getByLabelText } = render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Enter test message
    const messageInput = screen.getByLabelText('User Message');
    await waitFor(() => {
      expect(messageInput).toBeInTheDocument();
    });

    // Simulate user input
    // Use userEvent directly (v13 API)
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for test to complete
    await waitFor(() => {
      expect(api.modelConfigAPI.testWithFile).toHaveBeenCalled();
    }, { timeout: 3000 });

    // Verify: Test endpoint was called with correct parameters (FormData)
    expect(api.modelConfigAPI.testWithFile).toHaveBeenCalledWith(
      testConfig.id,
      expect.any(FormData)
    );

    // Verify: Results are displayed
    await waitFor(() => {
      expect(screen.getByText('Test response from LLM')).toBeInTheDocument();
    });
  });

  test('test endpoint handles failure gracefully', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-cleanup-2',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-cleanup-2',
      name: 'Cleanup Fail Test Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    const testResult = {
      success: false,
      error: 'Invalid API key',
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
      testWithFile: jest.fn().mockResolvedValue({ data: testResult }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component
    const { getByText, getByLabelText } = render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Enter test message
    const messageInput = screen.getByLabelText('User Message');
    await waitFor(() => {
      expect(messageInput).toBeInTheDocument();
    });

    // Simulate user input
    // Use userEvent directly (v13 API)
    await userEvent.type(messageInput, 'Test message that will fail');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for test to complete
    await waitFor(() => {
      expect(api.modelConfigAPI.testWithFile).toHaveBeenCalled();
    }, { timeout: 3000 });

    // Verify: Error is displayed
    await waitFor(() => {
      expect(screen.getByText(/Invalid API key/i)).toBeInTheDocument();
    });

    // Verify: Component is still functional
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
  });

  test('component handles network error gracefully', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-cleanup-3',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-cleanup-3',
      name: 'Cleanup Error Test Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    // Mock API responses - test endpoint throws network error
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
      testWithFile: jest.fn().mockRejectedValue(new Error('Network error')),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component
    const { getByText, getByLabelText } = render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Enter test message
    const messageInput = screen.getByLabelText('User Message');
    await waitFor(() => {
      expect(messageInput).toBeInTheDocument();
    });

    // Simulate user input
    // Use userEvent directly (v13 API)
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for test to complete
    await waitFor(() => {
      expect(api.modelConfigAPI.testWithFile).toHaveBeenCalled();
    }, { timeout: 3000 });

    // Verify: Error is displayed
    await waitFor(() => {
      expect(screen.getByText(/Network error/i)).toBeInTheDocument();
    });

    // Verify: Component didn't crash
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
  });
});

/**
 * Unit Tests for Property 15: LLM Tester Error Resilience
 * 
 * Feature: open-source-fixes, Property 15: LLM Tester Error Resilience
 * Validates: Requirements 8.3
 * 
 * Property: For any error that occurs during LLM Tester execution,
 * the component should display the error without crashing.
 */

/**
 * Unit Tests for Property 15: LLM Tester Error Resilience
 * 
 * Feature: open-source-fixes, Property 15: LLM Tester Error Resilience
 * Validates: Requirements 8.3
 * 
 * Property: For any error that occurs during LLM Tester execution,
 * the component should display the error without crashing.
 * 
 * Note: The LLM Tester now uses the dedicated test endpoint (modelConfigAPI.test)
 * which returns structured error responses. These tests verify error handling.
 */
describe('LLMTester - Property 15: Error Resilience', () => {
  // Increase timeout for this suite due to multiple async operations
  jest.setTimeout(15000);

  beforeEach(() => {
    jest.clearAllMocks();
    
    // Mock extractDataFromResponse and extractItemsFromResponse
    api.extractDataFromResponse = jest.fn().mockImplementation((response) => response?.data);
    api.extractItemsFromResponse = jest.fn().mockImplementation((response) => response?.data || []);
    
    // Mock formatError
    api.formatError = jest.fn().mockImplementation((error) => error?.message || 'Unknown error');
  });

  test('component displays error when test endpoint returns failure', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-error-1',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-error-1',
      name: 'Error Test Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    const testResult = {
      success: false,
      error: 'Invalid API key or authentication failed',
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
      testWithFile: jest.fn().mockResolvedValue({ data: testResult }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component
    const { getByText, getByLabelText } = render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Enter test message
    const messageInput = screen.getByLabelText('User Message');
    await waitFor(() => {
      expect(messageInput).toBeInTheDocument();
    });

    // Simulate user input
    // Use userEvent directly (v13 API)
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for error to be displayed
    await waitFor(() => {
      expect(api.modelConfigAPI.testWithFile).toHaveBeenCalled();
    }, { timeout: 3000 });

    // Verify: Error message is displayed
    await waitFor(() => {
      expect(screen.getByText(/Invalid API key or authentication failed/i)).toBeInTheDocument();
    });

    // Verify: Component is still functional (not crashed)
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    expect(messageInput).toBeInTheDocument();
    expect(testButton).toBeInTheDocument();
  });

  test('component displays error when test endpoint throws exception', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-error-2',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-error-2',
      name: 'Exception Error Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    // Mock API responses - test endpoint throws exception
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
      testWithFile: jest.fn().mockRejectedValue(new Error('Server error: 500')),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component
    const { getByText, getByLabelText } = render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Enter test message
    const messageInput = screen.getByLabelText('User Message');
    await waitFor(() => {
      expect(messageInput).toBeInTheDocument();
    });

    // Simulate user input
    // Use userEvent directly (v13 API)
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for error to be displayed
    await waitFor(() => {
      expect(api.modelConfigAPI.testWithFile).toHaveBeenCalled();
    }, { timeout: 3000 });

    // Verify: Error message is displayed
    await waitFor(() => {
      expect(screen.getByText(/Server error: 500/i)).toBeInTheDocument();
    });

    // Verify: Component is still functional (not crashed)
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    expect(messageInput).toBeInTheDocument();
    expect(testButton).toBeInTheDocument();
  });

  test('component displays timeout error with special formatting', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-error-3',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-error-3',
      name: 'Timeout Error Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    const testResult = {
      success: false,
      error: 'Request timed out after 30 seconds',
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
      testWithFile: jest.fn().mockResolvedValue({ data: testResult }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component
    const { getByText, getByLabelText } = render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Enter test message
    const messageInput = screen.getByLabelText('User Message');
    await waitFor(() => {
      expect(messageInput).toBeInTheDocument();
    });

    // Simulate user input
    // Use userEvent directly (v13 API)
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for error to be displayed
    await waitFor(() => {
      expect(api.modelConfigAPI.testWithFile).toHaveBeenCalled();
    }, { timeout: 3000 });

    // Verify: Timeout error is displayed with special formatting
    await waitFor(() => {
      const timeoutElements = screen.getAllByText(/Request Timed Out/i);
      expect(timeoutElements.length).toBeGreaterThan(0);
    });

    // Verify: Component is still functional (not crashed)
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    expect(messageInput).toBeInTheDocument();
    expect(testButton).toBeInTheDocument();
  });

  test('component handles network timeout gracefully', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-error-4',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-error-4',
      name: 'Network Timeout Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    // Mock API responses - test endpoint throws timeout error
    const timeoutError = new Error('timeout of 30000ms exceeded');
    timeoutError.code = 'ECONNABORTED';
    
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
      testWithFile: jest.fn().mockRejectedValue(timeoutError),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component
    const { getByText, getByLabelText } = render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Enter test message
    const messageInput = screen.getByLabelText('User Message');
    await waitFor(() => {
      expect(messageInput).toBeInTheDocument();
    });

    // Simulate user input
    // Use userEvent directly (v13 API)
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for error to be displayed
    await waitFor(() => {
      expect(api.modelConfigAPI.testWithFile).toHaveBeenCalled();
    }, { timeout: 3000 });

    // Verify: Timeout error is displayed
    await waitFor(() => {
      const timeoutElements = screen.getAllByText(/Request Timed Out/i);
      expect(timeoutElements.length).toBeGreaterThan(0);
    });

    // Verify: Component is still functional (not crashed)
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    expect(messageInput).toBeInTheDocument();
    expect(testButton).toBeInTheDocument();
  });

  test('component can recover and run another test after error', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-error-5',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-error-5',
      name: 'Recovery Test Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    const failResult = {
      success: false,
      error: 'First call failed',
    };

    const successResult = {
      success: true,
      response: 'Test response after recovery',
      model_used: 'gpt-4',
      token_usage: { total_tokens: 100 },
      response_time_ms: 500,
      prompt_applied: false,
    };

    // Mock API responses - first call fails, second succeeds
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
      testWithFile: jest.fn()
        .mockResolvedValueOnce({ data: failResult })
        .mockResolvedValueOnce({ data: successResult }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI.list = jest.fn().mockResolvedValue({ data: [] });

    // Render component
    const { getByText, getByLabelText } = render(
      <TestWrapper>
        <LLMTester prePopulatedConfigId={testConfig.id} />
      </TestWrapper>
    );

    // Wait for component to load
    await waitFor(() => {
      expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    });

    // Enter test message
    const messageInput = screen.getByLabelText('User Message');
    await waitFor(() => {
      expect(messageInput).toBeInTheDocument();
    });

    // Simulate user input
    // Use userEvent directly (v13 API)
    await userEvent.type(messageInput, 'First test');

    // Click test button (first attempt - will fail)
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for error to be displayed
    await waitFor(() => {
      expect(api.modelConfigAPI.testWithFile).toHaveBeenCalledTimes(1);
    }, { timeout: 3000 });

    // Verify: First call failed
    await waitFor(() => {
      expect(screen.getByText(/First call failed/i)).toBeInTheDocument();
    });

    // Clear the message input and enter new message
    await userEvent.clear(messageInput);
    await userEvent.type(messageInput, 'Second test');

    // Click test button again (second attempt - will succeed)
    await userEvent.click(testButton);

    // Wait for success
    await waitFor(() => {
      expect(api.modelConfigAPI.testWithFile).toHaveBeenCalledTimes(2);
    }, { timeout: 3000 });

    // Verify: Component recovered - check that success response is displayed
    await waitFor(() => {
      expect(screen.getByText('Test response after recovery')).toBeInTheDocument();
    }, { timeout: 2000 });

    // Verify: Component is still functional
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    expect(messageInput).toBeInTheDocument();
    expect(testButton).toBeInTheDocument();
  });
});
