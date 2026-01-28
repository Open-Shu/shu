/**
 * Provider setup instructions and connection test suggestions
 * Used across LLM provider configuration UI
 */

// Provider-specific setup instructions
export const PROVIDER_SETUP_INSTRUCTIONS = {
  openai: {
    title: 'OpenAI Setup',
    steps: [
      'Sign up or log in at platform.openai.com',
      'Navigate to API Keys section (platform.openai.com/api-keys)',
      'Click "Create new secret key" and copy it immediately',
      'Paste the key below (starts with "sk-", "sk-proj-", or "sk-admin-")',
      'Set your API endpoint to https://api.openai.com/v1 (default)'
    ],
    apiKeyFormat: 'sk-, sk-proj-, sk-admin-',
    apiKeyUrl: 'https://platform.openai.com/api-keys',
    defaultEndpoint: 'https://api.openai.com/v1',
    testTips: [
      'Ensure your API key has sufficient credits',
      'Check that your organization ID is correct (if using one)',
      'Verify your network can reach api.openai.com',
      'OpenAI keys may start with sk-, sk-proj-, or sk-admin- depending on key type'
    ]
  },
  anthropic: {
    title: 'Anthropic Setup',
    steps: [
      'Sign up or log in at console.anthropic.com',
      'Navigate to Settings → API Keys',
      'Click "Create Key" and copy it immediately',
      'Paste the key below (starts with "sk-ant-")',
      'Set your API endpoint to https://api.anthropic.com (default)'
    ],
    apiKeyFormat: 'sk-ant-...',
    apiKeyUrl: 'https://console.anthropic.com/settings/keys',
    defaultEndpoint: 'https://api.anthropic.com',
    testTips: [
      'Ensure your API key has sufficient credits',
      'Anthropic requires max_tokens parameter - this is set automatically',
      'Verify your network can reach api.anthropic.com'
    ]
  },
  ollama: {
    title: 'Ollama Setup',
    steps: [
      'Install Ollama from ollama.ai',
      'Start Ollama service (it runs on localhost:11434 by default)',
      'Pull models using: ollama pull <model-name>',
      'No API key needed for local Ollama',
      'Set your API endpoint to http://localhost:11434 (default)'
    ],
    apiKeyFormat: 'Not required',
    apiKeyUrl: null,
    defaultEndpoint: 'http://localhost:11434',
    testTips: [
      'Ensure Ollama service is running (check with: ollama list)',
      'Verify the endpoint URL matches your Ollama installation',
      'Make sure you have pulled at least one model',
      'Check firewall settings if using remote Ollama'
    ]
  },
  lm_studio: {
    title: 'LM Studio Setup',
    steps: [
      'Install LM Studio from lmstudio.ai',
      'Load a model in LM Studio',
      'Start the local server (usually on port 1234)',
      'No API key needed for local LM Studio',
      'Set your API endpoint to http://localhost:1234/v1 (default)'
    ],
    apiKeyFormat: 'Not required',
    apiKeyUrl: null,
    defaultEndpoint: 'http://localhost:1234/v1',
    testTips: [
      'Ensure LM Studio server is running',
      'Verify a model is loaded in LM Studio',
      'Check the port number in LM Studio settings',
      'Verify your network can reach localhost'
    ]
  },
  generic_completions: {
    title: 'Generic Completions Setup',
    steps: [
      'This is for OpenAI-compatible APIs',
      'Get your API endpoint from your provider',
      'Get your API key from your provider',
      'Configure the endpoint and key below',
      'Test the connection to verify it works'
    ],
    apiKeyFormat: 'Provider-specific',
    apiKeyUrl: null,
    defaultEndpoint: 'Provider-specific',
    testTips: [
      'Verify the API endpoint URL is correct',
      'Ensure your API key is valid',
      'Check that the provider uses OpenAI-compatible format',
      'Test with a simple model first'
    ]
  }
};

// Common connection test failure suggestions
export const CONNECTION_TEST_SUGGESTIONS = {
  401: {
    title: 'Authentication Failed',
    suggestions: [
      'Verify your API key is correct and has not expired',
      'Check that you copied the entire key without extra spaces',
      'For OpenAI: Ensure the key starts with "sk-"',
      'For Anthropic: Ensure the key starts with "sk-ant-"',
      'Regenerate your API key if needed and try again'
    ]
  },
  403: {
    title: 'Access Forbidden',
    suggestions: [
      'Your API key may not have permission for this operation',
      'Check your account status and billing',
      'Verify your organization ID is correct (if using one)',
      'Contact your provider support if the issue persists'
    ]
  },
  404: {
    title: 'Endpoint Not Found',
    suggestions: [
      'Verify the API endpoint URL is correct',
      'Check for typos in the endpoint URL',
      'Ensure you are using the correct base URL for your provider',
      'For Azure: Verify your resource name and region are correct'
    ]
  },
  429: {
    title: 'Rate Limit Exceeded',
    suggestions: [
      'You have exceeded your rate limit',
      'Wait a few moments before trying again',
      'Consider upgrading your plan for higher limits',
      'Adjust your rate limit settings in the provider configuration'
    ]
  },
  500: {
    title: 'Provider Server Error',
    suggestions: [
      'The provider is experiencing issues',
      'Wait a few moments and try again',
      'Check the provider status page for known issues',
      'Try again later if the problem persists'
    ]
  },
  timeout: {
    title: 'Connection Timeout',
    suggestions: [
      'Check your network connection',
      'Verify the endpoint URL is reachable',
      'For local providers: Ensure the service is running',
      'Check firewall settings that may block the connection',
      'Try increasing the timeout value if the provider is slow'
    ]
  },
  connection_refused: {
    title: 'Connection Refused',
    suggestions: [
      'For local providers: Ensure the service is running (e.g., Ollama, LM Studio)',
      'Verify the endpoint URL and port number are correct',
      'Check that the service is listening on the specified port',
      'For remote providers: Verify the server is accessible',
      'Check firewall settings that may block the connection'
    ]
  },
  network: {
    title: 'Network Error',
    suggestions: [
      'Check your internet connection',
      'Verify the endpoint URL is correct',
      'For local providers: Ensure the service is running',
      'Check DNS resolution for the endpoint',
      'Verify firewall or proxy settings'
    ]
  }
};

/**
 * Get setup instructions for a provider type
 * @param {string} providerType - The provider type key
 * @returns {object|null} Setup instructions or null if not found
 */
export const getProviderSetupInstructions = (providerType) => {
  return PROVIDER_SETUP_INSTRUCTIONS[providerType] || null;
};

/**
 * Get connection test suggestions based on error
 * @param {number|string} statusCodeOrType - HTTP status code or error type
 * @returns {object|null} Suggestions object or null if not found
 */
export const getConnectionTestSuggestions = (statusCodeOrType) => {
  return CONNECTION_TEST_SUGGESTIONS[statusCodeOrType] || null;
};

/**
 * Format connection test error with suggestions
 * @param {Error} error - The error object
 * @param {number} statusCode - HTTP status code
 * @returns {object} Formatted error with suggestions
 */
export const formatConnectionTestError = (error, statusCode) => {
  let suggestions = [];
  
  if (statusCode === 401) {
    suggestions = CONNECTION_TEST_SUGGESTIONS[401].suggestions;
  } else if (statusCode === 403) {
    suggestions = CONNECTION_TEST_SUGGESTIONS[403].suggestions;
  } else if (statusCode === 404) {
    suggestions = CONNECTION_TEST_SUGGESTIONS[404].suggestions;
  } else if (statusCode === 429) {
    suggestions = CONNECTION_TEST_SUGGESTIONS[429].suggestions;
  } else if (statusCode >= 500) {
    suggestions = CONNECTION_TEST_SUGGESTIONS[500].suggestions;
  } else if (error.code === 'ECONNREFUSED') {
    suggestions = CONNECTION_TEST_SUGGESTIONS.connection_refused.suggestions;
  } else if (error.message?.includes('timeout')) {
    suggestions = CONNECTION_TEST_SUGGESTIONS.timeout.suggestions;
  } else {
    suggestions = CONNECTION_TEST_SUGGESTIONS.network.suggestions;
  }
  
  return {
    message: error.message,
    suggestions: suggestions
  };
};
