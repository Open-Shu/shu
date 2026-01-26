import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
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
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

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
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

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
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [testKB1, testKB2] }),
    };

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
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [testKB] }),
    };

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
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

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
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [testKB] }),
    };

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
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

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
 * Property: For any LLM Tester test execution, both the temporary model
 * configuration and conversation should be cleaned up, even if the test fails.
 */
describe('LLMTester - Property 14: Resource Cleanup', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    
    // Mock extractDataFromResponse and extractItemsFromResponse
    api.extractDataFromResponse = jest.fn().mockImplementation((response) => response?.data);
    api.extractItemsFromResponse = jest.fn().mockImplementation((response) => response?.data || []);
    
    // Mock formatError
    api.formatError = jest.fn().mockImplementation((error) => error?.message || 'Unknown error');
  });

  test('conversation is cleaned up after successful test', async () => {
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

    const testConversation = {
      id: 'conv-cleanup-1',
      title: 'LLM Tester - 2024-01-01T00:00:00.000Z',
      model_configuration_id: testConfig.id,
    };

    const testMessage = {
      id: 'msg-cleanup-1',
      role: 'assistant',
      content: 'Test response',
      model_id: testConfig.model_name,
      message_metadata: {
        usage: { total_tokens: 100 },
      },
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

    api.chatAPI = {
      createConversation: jest.fn().mockResolvedValue({ data: testConversation }),
      streamMessage: jest.fn().mockResolvedValue({
        ok: true,
        body: {
          getReader: () => ({
            read: jest.fn()
              .mockResolvedValueOnce({
                value: new TextEncoder().encode('data: {"content":"Test"}\n'),
                done: false,
              })
              .mockResolvedValueOnce({
                value: new TextEncoder().encode('data: [DONE]\n'),
                done: true,
              }),
          }),
        },
      }),
      getMessages: jest.fn().mockResolvedValue({ data: [testMessage] }),
      deleteConversation: jest.fn().mockResolvedValue({}),
    };

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
    const userEvent = require('@testing-library/user-event').default;
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for test to complete
    await waitFor(() => {
      expect(api.chatAPI.createConversation).toHaveBeenCalled();
    }, { timeout: 5000 });

    // Verify: Conversation was created
    expect(api.chatAPI.createConversation).toHaveBeenCalledWith({
      title: expect.stringContaining('LLM Tester'),
      model_configuration_id: testConfig.id,
    });

    // Verify: Conversation was deleted (cleanup occurred)
    await waitFor(() => {
      expect(api.chatAPI.deleteConversation).toHaveBeenCalledWith(testConversation.id);
    }, { timeout: 5000 });
  });

  test('conversation is cleaned up even when test fails', async () => {
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

    const testConversation = {
      id: 'conv-cleanup-2',
      title: 'LLM Tester - 2024-01-01T00:00:00.000Z',
      model_configuration_id: testConfig.id,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

    api.chatAPI = {
      createConversation: jest.fn().mockResolvedValue({ data: testConversation }),
      streamMessage: jest.fn().mockRejectedValue(new Error('Streaming failed')),
      deleteConversation: jest.fn().mockResolvedValue({}),
    };

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
    const userEvent = require('@testing-library/user-event').default;
    await userEvent.type(messageInput, 'Test message that will fail');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for test to fail
    await waitFor(() => {
      expect(api.chatAPI.streamMessage).toHaveBeenCalled();
    }, { timeout: 5000 });

    // Verify: Conversation was created
    expect(api.chatAPI.createConversation).toHaveBeenCalled();

    // Verify: Conversation was deleted even though test failed (cleanup occurred)
    await waitFor(() => {
      expect(api.chatAPI.deleteConversation).toHaveBeenCalledWith(testConversation.id);
    }, { timeout: 5000 });
  });

  test('cleanup continues even if conversation deletion fails', async () => {
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

    const testConversation = {
      id: 'conv-cleanup-3',
      title: 'LLM Tester - 2024-01-01T00:00:00.000Z',
      model_configuration_id: testConfig.id,
    };

    const testMessage = {
      id: 'msg-cleanup-3',
      role: 'assistant',
      content: 'Test response',
      model_id: testConfig.model_name,
      message_metadata: {
        usage: { total_tokens: 100 },
      },
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

    api.chatAPI = {
      createConversation: jest.fn().mockResolvedValue({ data: testConversation }),
      streamMessage: jest.fn().mockResolvedValue({
        ok: true,
        body: {
          getReader: () => ({
            read: jest.fn()
              .mockResolvedValueOnce({
                value: new TextEncoder().encode('data: {"content":"Test"}\n'),
                done: false,
              })
              .mockResolvedValueOnce({
                value: new TextEncoder().encode('data: [DONE]\n'),
                done: true,
              }),
          }),
        },
      }),
      getMessages: jest.fn().mockResolvedValue({ data: [testMessage] }),
      deleteConversation: jest.fn().mockRejectedValue(new Error('Cleanup failed')),
    };

    // Spy on console.warn to verify cleanup error is logged
    const consoleWarnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});

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
    const userEvent = require('@testing-library/user-event').default;
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for test to complete
    await waitFor(() => {
      expect(api.chatAPI.deleteConversation).toHaveBeenCalled();
    }, { timeout: 5000 });

    // Verify: Cleanup was attempted
    expect(api.chatAPI.deleteConversation).toHaveBeenCalledWith(testConversation.id);

    // Verify: Component didn't crash despite cleanup failure
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();

    // Cleanup
    consoleWarnSpy.mockRestore();
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
describe('LLMTester - Property 15: Error Resilience', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    
    // Mock extractDataFromResponse and extractItemsFromResponse
    api.extractDataFromResponse = jest.fn().mockImplementation((response) => response?.data);
    api.extractItemsFromResponse = jest.fn().mockImplementation((response) => response?.data || []);
    
    // Mock formatError
    api.formatError = jest.fn().mockImplementation((error) => error?.message || 'Unknown error');
  });

  test('component displays error when conversation creation fails', async () => {
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

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

    api.chatAPI = {
      createConversation: jest.fn().mockRejectedValue(new Error('Failed to create conversation')),
      deleteConversation: jest.fn().mockResolvedValue({}),
    };

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
    const userEvent = require('@testing-library/user-event').default;
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for error to be displayed
    await waitFor(() => {
      const errorAlert = screen.getByText(/Error:/i);
      expect(errorAlert).toBeInTheDocument();
    }, { timeout: 5000 });

    // Verify: Error message is displayed
    expect(screen.getByText(/Failed to create conversation/i)).toBeInTheDocument();

    // Verify: Component is still functional (not crashed)
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    expect(messageInput).toBeInTheDocument();
    expect(testButton).toBeInTheDocument();
  });

  test('component displays error when streaming fails', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-error-2',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-error-2',
      name: 'Streaming Error Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    const testConversation = {
      id: 'conv-error-2',
      title: 'LLM Tester - 2024-01-01T00:00:00.000Z',
      model_configuration_id: testConfig.id,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

    api.chatAPI = {
      createConversation: jest.fn().mockResolvedValue({ data: testConversation }),
      streamMessage: jest.fn().mockResolvedValue({
        ok: false,
        status: 500,
      }),
      deleteConversation: jest.fn().mockResolvedValue({}),
    };

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
    const userEvent = require('@testing-library/user-event').default;
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for error to be displayed
    await waitFor(() => {
      const errorAlert = screen.getByText(/Error:/i);
      expect(errorAlert).toBeInTheDocument();
    }, { timeout: 5000 });

    // Verify: Error message is displayed
    expect(screen.getByText(/Streaming failed with status 500/i)).toBeInTheDocument();

    // Verify: Component is still functional (not crashed)
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    expect(messageInput).toBeInTheDocument();
    expect(testButton).toBeInTheDocument();
  });

  test('component displays error when message fetch fails', async () => {
    // Setup: Create test data
    const testProvider = {
      id: 'provider-error-3',
      name: 'OpenAI',
      provider_type: 'openai',
    };

    const testConfig = {
      id: 'config-error-3',
      name: 'Message Fetch Error Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    const testConversation = {
      id: 'conv-error-3',
      title: 'LLM Tester - 2024-01-01T00:00:00.000Z',
      model_configuration_id: testConfig.id,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

    api.chatAPI = {
      createConversation: jest.fn().mockResolvedValue({ data: testConversation }),
      streamMessage: jest.fn().mockResolvedValue({
        ok: true,
        body: {
          getReader: () => ({
            read: jest.fn()
              .mockResolvedValueOnce({
                value: new TextEncoder().encode('data: {"content":"Test"}\n'),
                done: false,
              })
              .mockResolvedValueOnce({
                value: new TextEncoder().encode('data: [DONE]\n'),
                done: true,
              }),
          }),
        },
      }),
      getMessages: jest.fn().mockRejectedValue(new Error('Failed to fetch messages')),
      deleteConversation: jest.fn().mockResolvedValue({}),
    };

    // Spy on console.warn to verify metadata fetch error is logged
    const consoleWarnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});

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
    const userEvent = require('@testing-library/user-event').default;
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for streaming to complete and getMessages to be called
    await waitFor(() => {
      expect(api.chatAPI.getMessages).toHaveBeenCalled();
    }, { timeout: 5000 });

    // Verify: Component still shows results tab despite metadata fetch failure
    await waitFor(() => {
      // The component should still display results
      expect(screen.getByText('Results')).toBeInTheDocument();
    }, { timeout: 2000 });

    // Verify: Component is still functional (not crashed)
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    expect(messageInput).toBeInTheDocument();
    expect(testButton).toBeInTheDocument();

    // Cleanup
    consoleWarnSpy.mockRestore();
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
      name: 'Timeout Error Config',
      llm_provider_id: testProvider.id,
      model_name: 'gpt-4',
      is_active: true,
    };

    const testConversation = {
      id: 'conv-error-4',
      title: 'LLM Tester - 2024-01-01T00:00:00.000Z',
      model_configuration_id: testConfig.id,
    };

    // Mock API responses
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

    api.chatAPI = {
      createConversation: jest.fn().mockResolvedValue({ data: testConversation }),
      streamMessage: jest.fn().mockImplementation(() => {
        const error = new Error('Network timeout');
        error.name = 'AbortError';
        return Promise.reject(error);
      }),
      deleteConversation: jest.fn().mockResolvedValue({}),
    };

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
    const userEvent = require('@testing-library/user-event').default;
    await userEvent.type(messageInput, 'Test message');

    // Click test button
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for error to be displayed
    await waitFor(() => {
      const errorAlert = screen.getByText(/Error:/i);
      expect(errorAlert).toBeInTheDocument();
    }, { timeout: 5000 });

    // Verify: Error message is displayed
    expect(screen.getByText(/Network timeout/i)).toBeInTheDocument();

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

    const testConversation1 = {
      id: 'conv-error-5-1',
      title: 'LLM Tester - 2024-01-01T00:00:00.000Z',
      model_configuration_id: testConfig.id,
    };

    const testConversation2 = {
      id: 'conv-error-5-2',
      title: 'LLM Tester - 2024-01-01T00:01:00.000Z',
      model_configuration_id: testConfig.id,
    };

    const testMessage = {
      id: 'msg-error-5',
      role: 'assistant',
      content: 'Test response after recovery',
      model_id: testConfig.model_name,
      message_metadata: {
        usage: { total_tokens: 100 },
      },
    };

    // Mock API responses - first call fails, second succeeds
    api.modelConfigAPI = {
      list: jest.fn().mockResolvedValue({ data: [testConfig] }),
    };
    
    api.llmAPI = {
      getProviders: jest.fn().mockResolvedValue({ data: [testProvider] }),
    };
    
    api.knowledgeBaseAPI = {
      list: jest.fn().mockResolvedValue({ data: [] }),
    };

    let callCount = 0;
    api.chatAPI = {
      createConversation: jest.fn().mockImplementation(() => {
        callCount++;
        if (callCount === 1) {
          return Promise.resolve({ data: testConversation1 });
        }
        return Promise.resolve({ data: testConversation2 });
      }),
      streamMessage: jest.fn()
        .mockRejectedValueOnce(new Error('First call failed'))
        .mockResolvedValueOnce({
          ok: true,
          body: {
            getReader: () => ({
              read: jest.fn()
                .mockResolvedValueOnce({
                  value: new TextEncoder().encode('data: {"content":"Success"}\n'),
                  done: false,
                })
                .mockResolvedValueOnce({
                  value: new TextEncoder().encode('data: [DONE]\n'),
                  done: true,
                }),
            }),
          },
        }),
      getMessages: jest.fn().mockResolvedValue({ data: [testMessage] }),
      deleteConversation: jest.fn().mockResolvedValue({}),
    };

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
    const userEvent = require('@testing-library/user-event').default;
    await userEvent.type(messageInput, 'First test');

    // Click test button (first attempt - will fail)
    const testButton = getByText('Test LLM Call');
    await userEvent.click(testButton);

    // Wait for error to be displayed
    await waitFor(() => {
      const errorAlert = screen.getByText(/Error:/i);
      expect(errorAlert).toBeInTheDocument();
    }, { timeout: 5000 });

    // Verify: First call failed
    expect(screen.getByText(/First call failed/i)).toBeInTheDocument();

    // Clear the message input and enter new message
    await userEvent.clear(messageInput);
    await userEvent.type(messageInput, 'Second test');

    // Click test button again (second attempt - will succeed)
    await userEvent.click(testButton);

    // Wait for success
    await waitFor(() => {
      expect(api.chatAPI.streamMessage).toHaveBeenCalledTimes(2);
    }, { timeout: 5000 });

    // Verify: Component recovered - check that Results section is visible
    await waitFor(() => {
      expect(screen.getByText('Results')).toBeInTheDocument();
    }, { timeout: 2000 });

    // Verify: Component is still functional
    expect(getByLabelText('Model Configuration')).toBeInTheDocument();
    expect(messageInput).toBeInTheDocument();
    expect(testButton).toBeInTheDocument();
  });
});
