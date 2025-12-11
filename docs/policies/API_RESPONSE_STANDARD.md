# API Response Standard

## Overview

Shu follows a standardized API response envelope format to ensure consistency across all endpoints and align with OpenAPI best practices. This format is designed to work seamlessly with both backend services and frontend applications.

**Note**: This document provides detailed API response standards referenced in `DEVELOPMENT_STANDARDS.md` section 16. For general development practices, see the main development standards document.

## Response Envelope Format

### Success Response
All successful API responses use the following envelope structure (single data layer; no success flag, no double-wrapping):

```json
{
  "data": {
    // Actual response data here
  }
}
```

### Error Response
All error responses use the following envelope structure:

```json
{
  "error": {
    "message": "Error description",
    "code": "ERROR_CODE",
    "details": {} // Optional additional error details
  }
}
```

## Implementation Details

### Backend Implementation (FastAPI)

#### Envelope Schemas
Defined in `src/shu/schemas/envelope.py`:

```python
from pydantic import BaseModel
from typing import Generic, TypeVar, Optional, Any, Dict

T = TypeVar('T')

class SuccessResponse(BaseModel, Generic[T]):
    data: T

class ErrorResponse(BaseModel):
    error: Dict[str, Any]
    meta: Optional[Dict[str, Any]] = None
```

#### Response Helpers
Use `ShuResponse` in `src/shu/core/response.py` to avoid double-wrapping and enforce a single envelope:

```python
from shu.core.response import ShuResponse

return ShuResponse.success(result)          # 200
return ShuResponse.created(result)          # 201
return ShuResponse.no_content()             # 204 (no body)
return ShuResponse.error("message", code="VALIDATION_ERROR", status_code=422)
```

#### Endpoint Implementation
All endpoints should return with the helpers and declare envelope response models:

```python
from fastapi import APIRouter, HTTPException, status
from shu.schemas.envelope import SuccessResponse, ErrorResponse
from shu.core.response import ShuResponse

@router.get("/endpoint", response_model=SuccessResponse[MySchema])
async def get_data():
    try:
        result = await some_service_call()
        return ShuResponse.success(result)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed")
```

### Frontend Implementation (React)

#### API Client Configuration
The frontend API client (`frontend/src/services/api.js`) includes utility functions to handle the envelope format:

```javascript
// Utility function to extract data from envelope format
export const extractDataFromResponse = (response) => {
  if (response && typeof response === 'object' && 'data' in response) {
    const firstData = response.data;
    if (firstData && typeof firstData === 'object' && 'data' in firstData) {
      return firstData.data;
    }
    return firstData;
  }
  return response;
};

// Utility function to extract items from paginated response
export const extractItemsFromResponse = (response) => {
  const data = extractDataFromResponse(response);
  if (data && typeof data === 'object' && 'items' in data) {
    return data.items;
  }
  if (Array.isArray(data)) {
    return data;
  }
  return [];
};
```

#### Component Usage
React components use these utilities to handle API responses:

```javascript
import { useQuery } from 'react-query';
import { extractDataFromResponse, extractItemsFromResponse } from '../services/api';

function MyComponent() {
  const { data: response } = useQuery('myData', api.getMyData);
  
  // Extract data from envelope format
  const data = extractDataFromResponse(response);
  const items = extractItemsFromResponse(response);
  
  return (
    <div>
      {items.map(item => (
        <div key={item.id}>{item.name}</div>
      ))}
    </div>
  );
}
```

## Response Structure
All endpoints now return responses in the standardized envelope format:

**Success Response:**
```json
{
  "data": {
    // Actual response data here
  }
}
```

**Error Response:**
```json
{
  "error": {
    "message": "Error description",
    "code": "ERROR_CODE",
    "details": {}
  }
}
```

## Testing Strategy

### Backend Testing
All backend tests should expect the envelope format:

```python
def test_endpoint():
    response = client.get("/endpoint")
    assert response.status_code == 200
    assert "data" in response.json()
    # Access actual data via response.json()["data"]
```

### Frontend Testing
Frontend tests should use the envelope extraction utilities:

```javascript
import { render, screen } from '@testing-library/react';
import { extractDataFromResponse } from '../services/api';

test('component displays data correctly', () => {
  const mockResponse = { data: { items: [{ id: 1, name: 'Test' }] } };
  const data = extractDataFromResponse(mockResponse);
  
  render(<MyComponent data={data} />);
  expect(screen.getByText('Test')).toBeInTheDocument();
});
```

## Benefits

- **Consistent API Contract**: All endpoints follow the same response format
- **Better Error Handling**: Structured error responses with codes and details
- **Frontend Integration**: Seamless integration with React components
- **OpenAPI Compliance**: Standard format for API documentation
- **Future Extensibility**: Easy to add metadata, pagination, etc.
- **Type Safety**: Generic types ensure type safety across the stack

## Error Handling

### Backend Error Handling
```python
from fastapi import HTTPException
from .schemas import ErrorResponse

@router.get("/endpoint")
async def get_data():
    try:
        result = await some_service_call()
        return SuccessResponse(data=result)
    except ValueError as e:
        raise HTTPException(
            status_code=400, 
            detail=ErrorResponse(
                error={
                    "message": str(e),
                    "code": "VALIDATION_ERROR",
                    "details": {"field": "value"}
                }
            ).dict()
        )
```

### Frontend Error Handling
```javascript
import { useQuery } from 'react-query';
import { formatError } from '../services/api';

function MyComponent() {
  const { data, error } = useQuery('myData', api.getMyData);
  
  if (error) {
    const errorInfo = formatError(error);
    return <div>Error: {errorInfo.message}</div>;
  }
  
  return <div>Data loaded successfully</div>;
}
```



## Agentic AI API Extensions

### Agent Event API Standards
For agentic AI functionality, additional API patterns are required:

#### Event Streaming API
```json
// WebSocket or Server-Sent Events for real-time agent updates
{
  "event": "agent_insight",
  "data": {
    "agent_id": "profile-user123",
    "insight_type": "collaboration_opportunity",
    "content": {
      "suggested_collaborator": "user456",
      "confidence": 0.85,
      "reasoning": "Both working on similar projects"
    }
  }
}
```

#### Agent Status API
```json
// GET /api/v1/agents/{agent_id}/status
{
  "data": {
    "agent_id": "profile-user123",
    "status": "healthy",
    "last_activity": "2025-01-21T10:30:00Z",
    "capabilities": ["user_profiling", "behavior_analysis"],
    "performance_metrics": {
      "avg_response_time": 1.2,
      "events_processed": 1547,
      "success_rate": 0.98
    }
  }
}
```

### Privacy-Aware Response Filtering
```json
// Responses must include privacy metadata
{
  "data": {
    "content": "Filtered content based on user permissions",
    "privacy_metadata": {
      "access_level": "user",
      "redacted_fields": ["email_addresses", "phone_numbers"],
      "source_kb": "user/123"
    }
  }
}
```

## Multi-Knowledge Base API Standards

### Cross-KB Query Response
```json
// GET /api/v1/query/multi-kb
{
  "data": {
    "results": [
      {
        "kb_id": "user/123",
        "kb_type": "personal",
        "results": [...],
        "result_count": 5
      },
      {
        "kb_id": "team/engineering",
        "kb_type": "team",
        "results": [...],
        "result_count": 3
      }
    ],
    "total_results": 8,
    "query_metadata": {
      "query_time": 0.45,
      "kbs_searched": 2,
      "privacy_filtered": true
    }
  }
}
```

### Knowledge Base Hierarchy Response
```json
// GET /api/v1/knowledge-bases/hierarchy
{
  "data": {
    "user_kbs": [
      {
        "id": "user/123",
        "name": "Personal Knowledge Base",
        "access_level": "owner"
      }
    ],
    "team_kbs": [
      {
        "id": "team/engineering",
        "name": "Engineering Team KB",
        "access_level": "member"
      }
    ],
    "company_kbs": [
      {
        "id": "company/policies",
        "name": "Company Policies",
        "access_level": "read"
      }
    ]
  }
}
```

## Error Code Standards

### Standard Error Codes
```json
{
  "error": {
    "message": "User-friendly error message",
    "code": "STANDARD_ERROR_CODE",
    "details": {
      "field": "specific_field",
      "value": "invalid_value"
    }
  }
}
```

#### Authentication & Authorization
- `AUTH_REQUIRED` - Authentication required
- `AUTH_INVALID` - Invalid authentication credentials
- `ACCESS_DENIED` - Insufficient permissions
- `KB_ACCESS_DENIED` - No access to specific knowledge base

## Authentication API Examples

### POST /api/v1/auth/login (Google OAuth)
Authenticate user with Google OAuth token.

**Request:**
```json
{
  "google_token": "string"
}
```

**Success Response (200):**
```json
{
  "data": {
    "access_token": "string",
    "refresh_token": "string",
    "token_type": "bearer",
    "user": {
      "id": "string",
      "email": "string",
      "name": "string",
      "role": "admin|power_user|regular_user",
      "is_active": true,
      "auth_method": "google",
      "google_id": "string",
      "picture_url": "string"
    }
  }
}
```

### POST /api/v1/auth/register (Password Registration)
Register a new user with email and password. **Security Note:** New users are created as inactive and require administrator activation.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "secure_password",
  "name": "User Name"
}
```

**Success Response (200):**
```json
{
  "data": {
    "message": "Registration successful! Your account has been created but requires administrator activation before you can log in.",
    "email": "user@example.com",
    "status": "pending_activation"
  }
}
```

### POST /api/v1/auth/login/password (Password Login)
Authenticate user with email and password.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "secure_password"
}
```

**Success Response (200):**
```json
{
  "data": {
    "access_token": "string",
    "refresh_token": "string",
    "token_type": "bearer",
    "user": {
      "id": "string",
      "email": "user@example.com",
      "name": "User Name",
      "role": "regular_user",
      "is_active": true,
      "auth_method": "password",
      "google_id": null,
      "picture_url": null
    }
  }
}
```

### PUT /api/v1/auth/change-password
Change current user's password (requires authentication).

**Request:**
```json
{
  "old_password": "current_password",
  "new_password": "new_secure_password"
}
```

**Success Response (200):**
```json
{
  "data": {
    "message": "Password changed successfully"
  }
}
```

### POST /api/v1/auth/users (Admin Only)
Create a new user account (admin only).

**Request:**
```json
{
  "email": "investor@example.com",
  "name": "Investor Name",
  "role": "regular_user",
  "password": "secure_password",
  "auth_method": "password"
}
```

**Success Response (200):**
```json
{
  "data": {
    "id": "string",
    "email": "investor@example.com",
    "name": "Investor Name",
    "role": "regular_user",
    "is_active": true,
    "auth_method": "password",
    "google_id": null,
    "picture_url": null
  }
}
```

#### Validation Errors
- `VALIDATION_ERROR` - General validation error
- `REQUIRED_FIELD` - Required field missing
- `INVALID_FORMAT` - Invalid field format
- `INVALID_VALUE` - Invalid field value

#### Agent-Specific Errors
- `AGENT_UNAVAILABLE` - Agent is not available
- `AGENT_ERROR` - Agent processing error
- `EVENT_PROCESSING_ERROR` - Event processing failed
- `PRIVACY_VIOLATION` - Privacy policy violation

#### System Errors
- `INTERNAL_ERROR` - Internal server error
- `SERVICE_UNAVAILABLE` - Service temporarily unavailable
- `RATE_LIMIT_EXCEEDED` - Rate limit exceeded
- `TIMEOUT_ERROR` - Request timeout

## Performance Standards

### Response Time Requirements
- **Simple queries**: <200ms
- **Multi-KB queries**: <500ms
- **Agent insights**: <2 seconds
- **Complex analytics**: <5 seconds

### Pagination Standards
```json
{
  "data": {
    "items": [...],
    "pagination": {
      "page": 1,
      "per_page": 20,
      "total_items": 150,
      "total_pages": 8,
      "has_next": true,
      "has_prev": false
    }
  }
}
```

### Caching Headers
```http
Cache-Control: public, max-age=300
ETag: "abc123def456"
Last-Modified: Mon, 21 Jan 2025 10:30:00 GMT
```

## Implementation Guidelines

1. **Always Use Envelope Format**: All API responses should use the envelope format
2. **Consistent Error Codes**: Use standardized error codes across all endpoints
3. **Frontend Utilities**: Use the provided frontend utilities for envelope handling
4. **Type Safety**: Leverage TypeScript (future) for better type safety
5. **Documentation**: Keep API documentation updated with response examples
6. **Testing**: Test both success and error scenarios with envelope format
7. **Privacy First**: Always include privacy metadata in responses
8. **Performance Monitoring**: Track and optimize API response times
9. **Agent Integration**: Design APIs to support real-time agent interactions
10. **Multi-KB Support**: Design responses to handle multiple knowledge base queries
