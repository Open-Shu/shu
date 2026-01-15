# Experience Model Configuration API Documentation

## Overview

This document describes the Experience API endpoints after the migration to use Model Configurations instead of direct LLM provider references. The Experience system now leverages the Model Configuration abstraction for better setup validation, parameter management, and user experience.

## Key Changes

### Schema Updates

The Experience API has been updated to use `model_configuration_id` instead of the previous `llm_provider_id` and `model_name` fields. This change provides:

- **Better Abstraction**: Users select from configured models like "Research Assistant" instead of specifying "OpenAI + gpt-4-turbo"
- **Centralized Configuration**: Model settings, prompts, and knowledge bases are managed in one place
- **Improved Validation**: System validates model configuration exists and is active before execution
- **Enhanced Traceability**: Experience runs store complete model configuration snapshots

## API Endpoints

### Create Experience

**Endpoint**: `POST /api/v1/experiences`

**Request Body**:
```json
{
  "name": "Customer Support Bot",
  "description": "Automated customer support workflow",
  "model_configuration_id": "mc_abc123",
  "prompt_id": null,
  "inline_prompt_template": null,
  "trigger_type": "manual",
  "trigger_config": null,
  "include_previous_run": false,
  "max_run_seconds": 120,
  "token_budget": null,
  "steps": [
    {
      "step_type": "plugin",
      "config": {
        "plugin_id": "shu_gmail_digest",
        "function_name": "get_recent_emails"
      }
    }
  ]
}
```

**Response**:
```json
{
  "data": {
    "id": "exp_xyz789",
    "name": "Customer Support Bot",
    "description": "Automated customer support workflow",
    "model_configuration_id": "mc_abc123",
    "model_configuration": {
      "id": "mc_abc123",
      "name": "GPT-4 Research Assistant",
      "description": "Optimized for research tasks",
      "llm_provider": {
        "id": "provider_123",
        "name": "OpenAI"
      },
      "model_name": "gpt-4-turbo",
      "parameter_overrides": {
        "temperature": 0.7,
        "max_tokens": 2000
      },
      "knowledge_bases": [
        {
          "id": "kb_456",
          "name": "Product Documentation"
        }
      ]
    },
    "prompt_id": null,
    "inline_prompt_template": null,
    "created_by": "user_123",
    "visibility": "private",
    "trigger_type": "manual",
    "trigger_config": null,
    "include_previous_run": false,
    "max_run_seconds": 120,
    "token_budget": null,
    "version": 1,
    "is_active_version": true,
    "created_at": "2026-01-13T20:00:00Z",
    "updated_at": "2026-01-13T20:00:00Z"
  }
}
```

### Get Experience

**Endpoint**: `GET /api/v1/experiences/{experience_id}`

**Query Parameters**:
- `include_relationships` (boolean, optional): Include model configuration details

**Response**:
```json
{
  "data": {
    "id": "exp_xyz789",
    "name": "Customer Support Bot",
    "description": "Automated customer support workflow",
    "model_configuration_id": "mc_abc123",
    "model_configuration": {
      "id": "mc_abc123",
      "name": "GPT-4 Research Assistant",
      "llm_provider": {
        "id": "provider_123",
        "name": "OpenAI"
      },
      "model_name": "gpt-4-turbo"
    },
    "created_by": "user_123",
    "visibility": "private",
    "version": 1,
    "is_active_version": true,
    "created_at": "2026-01-13T20:00:00Z",
    "updated_at": "2026-01-13T20:00:00Z"
  }
}
```

### List Experiences

**Endpoint**: `GET /api/v1/experiences`

**Query Parameters**:
- `include_relationships` (boolean, optional): Include model configuration details
- `limit` (integer, optional): Number of results per page
- `offset` (integer, optional): Pagination offset

**Response**:
```json
{
  "data": {
    "items": [
      {
        "id": "exp_xyz789",
        "name": "Customer Support Bot",
        "model_configuration_id": "mc_abc123",
        "model_configuration": {
          "id": "mc_abc123",
          "name": "GPT-4 Research Assistant",
          "llm_provider": {
            "name": "OpenAI"
          },
          "model_name": "gpt-4-turbo"
        },
        "created_at": "2026-01-13T20:00:00Z"
      }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0
  }
}
```

### Execute Experience

**Endpoint**: `POST /api/v1/experiences/{experience_id}/run`

**Request Body**:
```json
{
  "input_params": {
    "user_query": "What are the latest support tickets?"
  }
}
```

**Response**:
```json
{
  "data": {
    "id": "run_abc123",
    "experience_id": "exp_xyz789",
    "user_id": "user_123",
    "model_configuration_id": "mc_abc123",
    "status": "succeeded",
    "started_at": "2026-01-13T20:05:00Z",
    "finished_at": "2026-01-13T20:05:15Z",
    "input_params": {
      "user_query": "What are the latest support tickets?"
    },
    "step_states": {
      "step_0": "completed"
    },
    "step_outputs": {
      "step_0": {
        "emails": [...]
      }
    },
    "result_content": "Based on recent emails, here are the top support issues...",
    "result_metadata": {
      "model_configuration": {
        "id": "mc_abc123",
        "name": "GPT-4 Research Assistant",
        "description": "Optimized for research tasks",
        "provider_id": "provider_123",
        "provider_name": "OpenAI",
        "model_name": "gpt-4-turbo",
        "parameter_overrides": {
          "temperature": 0.7,
          "max_tokens": 2000
        }
      },
      "system_prompt_content": "You are a helpful research assistant...",
      "user_content": "Analyze these emails..."
    },
    "error_message": null
  }
}
```

## Field Descriptions

### Experience Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique experience identifier |
| `name` | string | Experience name (max 100 chars) |
| `description` | string | Optional description |
| `model_configuration_id` | string | Reference to model configuration (optional, null if no LLM synthesis) |
| `model_configuration` | object | Model configuration details (when `include_relationships=true`) |
| `prompt_id` | string | Optional prompt override (takes precedence over model config prompt) |
| `inline_prompt_template` | string | Optional inline prompt template (highest precedence) |
| `created_by` | string | User ID of creator |
| `visibility` | string | "private" or "shared" |
| `trigger_type` | string | "manual", "scheduled", or "webhook" |
| `trigger_config` | object | Configuration for trigger type |
| `include_previous_run` | boolean | Whether to include previous run context |
| `max_run_seconds` | integer | Maximum execution time (default: 120) |
| `token_budget` | integer | Optional token limit for LLM calls |
| `version` | integer | Version number |
| `is_active_version` | boolean | Whether this is the active version |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

### ExperienceRun Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique run identifier |
| `experience_id` | string | Reference to experience |
| `user_id` | string | User who triggered the run |
| `model_configuration_id` | string | Model configuration used for this run |
| `status` | string | "pending", "running", "succeeded", "failed" |
| `started_at` | datetime | Run start time |
| `finished_at` | datetime | Run completion time |
| `input_params` | object | Input parameters provided |
| `step_states` | object | State of each step |
| `step_outputs` | object | Output from each step |
| `result_content` | string | Final synthesized result |
| `result_metadata` | object | Metadata including model config snapshot |
| `error_message` | string | Error message if failed |

### Model Configuration Snapshot

The `result_metadata.model_configuration` object contains a snapshot of the model configuration at execution time:

```json
{
  "id": "mc_abc123",
  "name": "GPT-4 Research Assistant",
  "description": "Optimized for research tasks",
  "provider_id": "provider_123",
  "provider_name": "OpenAI",
  "model_name": "gpt-4-turbo",
  "parameter_overrides": {
    "temperature": 0.7,
    "max_tokens": 2000
  }
}
```

This snapshot ensures historical runs remain traceable even if the model configuration is later modified or deleted.

## Validation Rules

### Model Configuration Validation

When creating or updating an experience with a `model_configuration_id`:

1. **Existence Check**: Model configuration must exist
2. **Active Status**: Model configuration must be active (`is_active=true`)
3. **Provider Status**: Underlying LLM provider must be active
4. **Access Control**: User must have access to the model configuration
5. **Knowledge Base Access**: User must have access to all knowledge bases associated with the model configuration

### Prompt Resolution Priority

When an experience executes with LLM synthesis, prompts are resolved in this order (highest to lowest priority):

1. **Inline Prompt Template**: `inline_prompt_template` field on the experience
2. **Experience Prompt**: `prompt_id` field on the experience
3. **Model Configuration Prompt**: `prompt_id` field on the model configuration
4. **Default**: Empty system prompt

## Error Responses

### Model Configuration Not Found

```json
{
  "error": {
    "message": "Model configuration mc_abc123 not found",
    "code": "MODEL_CONFIGURATION_NOT_FOUND",
    "details": {
      "model_configuration_id": "mc_abc123"
    }
  }
}
```

### Inactive Model Configuration

```json
{
  "error": {
    "message": "Model configuration 'GPT-4 Research Assistant' is not active",
    "code": "MODEL_CONFIGURATION_INACTIVE",
    "details": {
      "model_configuration_id": "mc_abc123",
      "model_configuration_name": "GPT-4 Research Assistant"
    }
  }
}
```

### Inactive Provider

```json
{
  "error": {
    "message": "Model configuration has inactive provider",
    "code": "PROVIDER_INACTIVE",
    "details": {
      "model_configuration_id": "mc_abc123",
      "provider_id": "provider_123"
    }
  }
}
```

### Access Denied

```json
{
  "error": {
    "message": "Access denied to model configuration or associated knowledge bases",
    "code": "ACCESS_DENIED",
    "details": {
      "model_configuration_id": "mc_abc123"
    }
  }
}
```
