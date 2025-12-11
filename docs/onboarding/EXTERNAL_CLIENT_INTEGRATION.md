# External Client Integration Guide

This document describes how to access the Shu API from external clients (scripts, automation, third-party applications) rather than the built-in React frontend.

## Current Implementation Status

**Implementation Status: Partial**

The Shu API currently supports two authentication methods for external access:

| Feature | Status | Notes |
|---------|--------|-------|
| Global API Key (Tier 0) | Implemented | Single static key via environment variable |
| JWT Bearer Token | Implemented | Full authentication with user context |
| Per-User API Keys (Tier 1) | Not Implemented | Planned in SHU-121 |
| API Key Scopes | Not Implemented | Planned in SHU-121 |
| Per-Key Rate Limiting | Not Implemented | Planned in SHU-124 |
| Client SDKs | Not Implemented | Planned in Cross-Cutting initiatives |

## Authentication Methods

### Option 1: Global API Key (Tier 0)

The simplest method for external automation. Uses a single static API key configured via environment variables.

**Configuration (Server Side):**
```bash
# In .env or Kubernetes secrets
SHU_API_KEY=your-secret-api-key-here
SHU_API_KEY_USER_EMAIL=existing-user@example.com  # Must match an active user
```

**Usage:**
```bash
curl -X POST https://your-shu-instance/api/v1/chat/conversations/{id}/send \
  -H "Authorization: ApiKey your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "rag_rewrite_mode": "no_rag"}'
```

**Limitations:**
- Single global key (not per-user)
- No scopes or granular permissions
- No key rotation without service restart
- No audit trail per request
- Inherits all permissions of the mapped user

### Option 2: JWT Bearer Token

Full authentication using the standard login flow. Provides complete user context and audit trail.

**Step 1: Obtain a token via login:**
```bash
curl -X POST https://your-shu-instance/api/v1/auth/login/password \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "your-password"}'
```

**Response:**
```json
{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "refresh_token": "...",
    "token_type": "bearer",
    "expires_in": 3600
  }
}
```

**Step 2: Use the token:**
```bash
curl -X POST https://your-shu-instance/api/v1/chat/conversations/{id}/send \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "rag_rewrite_mode": "raw_query"}'
```

**Token Refresh:**
```bash
curl -X POST https://your-shu-instance/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "..."}'
```

## API Response Envelope

All Shu API responses use a consistent envelope format.

**Success Response:**
```json
{
  "data": { ... }
}
```

**Error Response:**
```json
{
  "error": {
    "message": "Error description",
    "code": "ERROR_CODE",
    "details": { ... }
  }
}
```

## Core Chat API

### Create a Conversation

```bash
POST /api/v1/chat/conversations
Content-Type: application/json
Authorization: ApiKey <key> | Bearer <jwt>

{
  "title": "My Conversation",
  "model_configuration_id": "<uuid>"
}
```

**Response:**
```json
{
  "data": {
    "id": "<conversation-uuid>",
    "user_id": "<user-uuid>",
    "title": "My Conversation",
    "model_configuration_id": "<model-config-uuid>",
    "is_active": true,
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T10:30:00Z"
  }
}
```

### Send Message (Streaming SSE)

```bash
POST /api/v1/chat/conversations/{conversation_id}/send
Content-Type: application/json
Authorization: ApiKey <key> | Bearer <jwt>

{
  "message": "What is the capital of France?",
  "rag_rewrite_mode": "no_rag",
  "knowledge_base_id": null,
  "attachment_ids": []
}
```

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | User message content |
| `rag_rewrite_mode` | string | No | RAG query strategy (see below) |
| `knowledge_base_id` | string | No | Specific KB for RAG (overrides model config) |
| `attachment_ids` | array | No | Attachment UUIDs to include as context |
| `client_temp_id` | string | No | Client-generated ID for optimistic UI |
| `ensemble_model_configuration_ids` | array | No | Additional models to execute in parallel |

**RAG Rewrite Modes:**

| Value | Description |
|-------|-------------|
| `no_rag` | Disable RAG entirely, use LLM only |
| `raw_query` | Pass query directly to vector search (default) |
| `distill_context` | Extract key facts before search |
| `rewrite_enhanced` | LLM rewrites query for better retrieval |

**Response (Server-Sent Events):**

The response is a stream of SSE events. Each event is a JSON object with an `event` type.

```
data: {"event": "user_message", "content": {...}, "client_temp_id": "..."}

data: {"event": "content_delta", "content": "The", "variant_index": 0, ...}

data: {"event": "content_delta", "content": " capital", "variant_index": 0, ...}

data: {"event": "final_message", "content": {...}, "variant_index": 0, ...}

data: [DONE]
```

**SSE Event Types:**

| Event Type | Description |
|------------|-------------|
| `user_message` | Echoes the saved user message with its ID |
| `content_delta` | Streaming token from the LLM response |
| `reasoning_delta` | Reasoning tokens (for models that support it) |
| `final_message` | Complete assistant message with metadata |
| `error` | Error occurred during processing |

**Final Message Content Structure:**
```json
{
  "event": "final_message",
  "variant_index": 0,
  "model_configuration_id": "<uuid>",
  "model_configuration": { "name": "GPT-4", ... },
  "model_name": "gpt-4",
  "model_display_name": "GPT-4 Turbo",
  "content": {
    "id": "<message-uuid>",
    "conversation_id": "<conv-uuid>",
    "role": "assistant",
    "content": "The capital of France is Paris.",
    "model_id": "gpt-4",
    "message_metadata": {...},
    "created_at": "2024-01-15T10:30:05Z"
  }
}
```

### List Conversations

```bash
GET /api/v1/chat/conversations?limit=20&offset=0
Authorization: ApiKey <key> | Bearer <jwt>
```

### Get Conversation Messages

```bash
GET /api/v1/chat/conversations/{conversation_id}/messages?limit=50&before_id=<uuid>
Authorization: ApiKey <key> | Bearer <jwt>
```

## Knowledge Base Query API

### Search a Knowledge Base

```bash
POST /api/v1/query/{knowledge_base_id}/search
Content-Type: application/json
Authorization: ApiKey <key> | Bearer <jwt>

{
  "query": "What are the project deadlines?",
  "query_type": "hybrid",
  "limit": 10,
  "similarity_threshold": 0.7
}
```

**Response:**
```json
{
  "data": {
    "results": [
      {
        "chunk_id": "<uuid>",
        "document_id": "<uuid>",
        "content": "...",
        "similarity_score": 0.85,
        "metadata": {...}
      }
    ],
    "total_results": 5
  }
}
```

## Error Handling

Common error codes your client should handle:

| HTTP Status | Error Code | Description |
|-------------|------------|-------------|
| 401 | `UNAUTHORIZED` | Invalid or missing authentication |
| 403 | `FORBIDDEN` | User lacks permission for this action |
| 404 | `NOT_FOUND` | Resource not found |
| 422 | `VALIDATION_ERROR` | Invalid request payload |
| 429 | `RATE_LIMITED` | Too many requests |
| 500 | `INTERNAL_ERROR` | Server error |

## Python Client Example

```python
import json
import requests
import sseclient

API_BASE = "https://your-shu-instance"
API_KEY = "your-api-key"

headers = {
    "Authorization": f"ApiKey {API_KEY}",
    "Content-Type": "application/json"
}

# Create conversation
resp = requests.post(
    f"{API_BASE}/api/v1/chat/conversations",
    headers=headers,
    json={"model_configuration_id": "<uuid>", "title": "API Test"}
)
conv_id = resp.json()["data"]["id"]

# Send message with streaming
resp = requests.post(
    f"{API_BASE}/api/v1/chat/conversations/{conv_id}/send",
    headers=headers,
    json={"message": "Hello!", "rag_rewrite_mode": "no_rag"},
    stream=True
)

client = sseclient.SSEClient(resp)
for event in client.events():
    if event.data == "[DONE]":
        break
    data = json.loads(event.data)
    if data.get("event") == "content_delta":
        print(data["content"], end="", flush=True)
```

## Known Limitations

1. **Single Global API Key** - Only one API key for the entire system
2. **No Key Rotation** - Changing the key requires environment update and restart
3. **No Per-Key Rate Limits** - Rate limiting is global, not per-key
4. **No Scopes** - API key inherits all permissions of the mapped user
5. **No Usage Tracking** - No audit trail for API key authenticated requests
6. **No Client SDKs** - No official Python, JavaScript, or other language SDKs

## Planned Enhancements

The following enhancements are planned in the **SHU-17 SECURITY-HARDENING** epic:

- **SHU-121**: User-specific API keys with scopes, expiration, and rate limits
- **SHU-120**: API key database schema
- **SHU-118**: API key management endpoints
- **SHU-119**: Unified authentication system
- **SHU-124**: Per-key rate limiting and quotas

The **Cross-Cutting** roadmap initiative includes:
- **Document SDK and APIs** (TODO) - Official client SDKs and API documentation

## Related Documentation

- [Onboarding Guide - Section 4](./onboarding/README.md#4-authentication-and-authorization-how-to-get-an-auth-token) - Authentication details
- [API Response Standard](./policies/API_RESPONSE_STANDARD.md) - Response envelope format
- [API Key Authentication Plan](./API_KEY_AUTHENTICATION_PLAN.md) - Future API key system design

