# Experience User Guide

## Overview

Experiences in Shu are configurable workflows that combine plugin operations, knowledge base queries, and AI synthesis to deliver specific outcomes. With the new Model Configuration integration, creating and managing experiences is now more intuitive and powerful.

## What's New: Model Configurations

### Before: Complex Setup
Previously, creating an experience required understanding technical details:
- Selecting specific LLM providers (OpenAI, Anthropic, etc.)
- Choosing exact model names (gpt-4-turbo, claude-3-sonnet, etc.)
- Manually configuring parameters for each experience

### Now: Simple Selection
With Model Configurations, you can:
- Choose from pre-configured options like "Research Assistant" or "Customer Support Bot"
- Leverage centralized settings including prompts, knowledge bases, and parameters
- Benefit from validated configurations that are guaranteed to work

## Getting Started

### Prerequisites

Before creating experiences, ensure you have:
1. **Model Configurations**: At least one active model configuration set up by your administrator
2. **Knowledge Bases**: Any knowledge bases you want to query (optional)
3. **Plugins**: Required plugins installed and configured - we will install missing plugins automatically in the future

### Creating Your First Experience

1. **Navigate to Experiences**
   - Go to the Experiences page in the admin panel
   - Click the "+ Create Experience" button

2. **Basic Information**
   - **Name**: Give your experience a descriptive name (e.g., "Daily Email Summary")
   - **Description**: Explain what the experience does and when to use it

3. **Model Configuration Selection**
   - **No LLM Synthesis**: Select this if your experience only uses plugins without AI analysis
   - **Choose Configuration**: Select from available model configurations like:
     - "Research Assistant" - Optimized for analysis and research tasks
     - "Customer Support Bot" - Tuned for helpful, professional responses
     - "Creative Writer" - Configured for creative and marketing content

4. **Configuration Details**
   When you select a model configuration, you'll see:
   - **Provider & Model**: The underlying AI service (e.g., "OpenAI - gpt-4-turbo")
   - **Knowledge Bases**: Any knowledge bases automatically included
   - **Description**: What this configuration is optimized for

5. **Advanced Options**
   - **Custom Prompt**: Override the model configuration's default prompt
   - **Execution Settings**: Adjust timeout and token limits if needed

6. **Experience Steps**
   - **Add Step**: Add steps in the order they should execute
   - **Plugin**: Execute plugins to inject data into the experience runtime
   - **Knowledge Base**: Execute knowledge base queries to inject data into the experience runtime

## Understanding Model Configurations

### What They Include

Each model configuration bundles:
- **AI Provider & Model**: The specific AI service and model version
- **System Prompt**: Instructions that guide the AI's behavior
- **Knowledge Bases**: Relevant information sources the AI can query
- **Parameters**: Settings like creativity level (temperature) and response length
- **Access Control**: Who can use this configuration

### Configuration Examples

**Research Assistant**
- Provider: OpenAI GPT-4 Turbo
- Optimized for: Analysis, fact-checking, detailed explanations
- Knowledge Bases: Company documentation, research papers
- Parameters: Lower creativity, longer responses

**Customer Support Bot**
- Provider: Anthropic Claude
- Optimized for: Helpful, professional customer interactions
- Knowledge Bases: Product manuals, FAQ database
- Parameters: Balanced creativity, concise responses

**Creative Writer**
- Provider: OpenAI GPT-4
- Optimized for: Marketing copy, creative content
- Knowledge Bases: Brand guidelines, style guides
- Parameters: Higher creativity, varied response lengths

## Using Experiences

### Manual Execution

1. **Find Your Experience**: Navigate to the Experiences list
2. **Click "Run"**: Start the experience execution
3. **Provide Input**: Enter any required parameters or context
4. **Monitor Progress**: Watch as steps execute in sequence
5. **Review Results**: See the final synthesized output

### Viewing Run History

Each experience maintains a complete history:
- **Execution Time**: When the experience ran
- **Status**: Success, failure, or in progress
- **Model Configuration**: Which configuration was used (with snapshot)
- **Duration**: How long the execution took
- **Results**: Complete output and intermediate steps

### Understanding Results

Experience results include:
- **Final Output**: The synthesized result from the AI
- **Step Details**: What each plugin or query step produced
- **Model Information**: Which AI model and settings were used
- **Metadata**: Execution context and configuration snapshot

## Best Practices

### Choosing Model Configurations

- **Match Purpose**: Select configurations optimized for your use case
- **Consider Knowledge**: Choose configurations with relevant knowledge bases
- **Test Different Options**: Try various configurations to find the best fit

### Designing Effective Experiences

1. **Clear Objectives**: Define what outcome you want to achieve
2. **Logical Flow**: Arrange steps in a sensible sequence
3. **Appropriate Data**: Include relevant plugins and knowledge sources
4. **Iterative Improvement**: Refine based on results and feedback

### Managing Experience Versions

- **Version Control**: Each change creates a new version
- **Active Versions**: Only one version is active at a time
- **History Preservation**: All versions and runs are preserved
- **Rollback Capability**: Can revert to previous versions if needed

## Troubleshooting

### Common Issues

**"Model configuration not found"**
- The selected model configuration may have been deleted
- Contact your administrator to restore or select a different configuration

**"Model configuration is not active"**
- The configuration has been disabled
- Choose an active configuration or ask your administrator to reactivate it

**"Access denied to model configuration"**
- You don't have permission to use this configuration
- Request access from your administrator or choose a different configuration

**"Provider inactive"**
- The underlying AI service is unavailable
- Try a configuration with a different provider or wait for service restoration

### Getting Help

1. **Check Run History**: Review previous successful runs for comparison
2. **Verify Configurations**: Ensure your model configuration is active and accessible
3. **Test Components**: Try individual plugins or queries separately
4. **Contact Support**: Reach out to your administrator with specific error messages

## Related Resources

- [Plugin Development Guide](./TUTORIAL_PLUGIN_DEVELOPMENT.md) - Creating custom plugins
- [API Documentation](./EXPERIENCE_MODEL_CONFIGURATION_API.md) - Technical API reference
