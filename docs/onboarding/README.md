# Shu Developer Onboarding Tutorial

This tutorial onboards a new engineer to Shu. It assumes a general software development foundation, but gives a high-level introduction to FastAPI, Pydantic, async SQLAlchemy, and AI/RAG concepts.

## What is Shu?

Shu is an **extensible AI operating system for work** that combines:
- **RAG (Retrieval-Augmented Generation)**: An AI technique that enhances language models by retrieving relevant information from knowledge bases before generating responses
- **Agentic AI**: AI systems that can take actions, make decisions, and work autonomously on behalf of users
- **Multi-modal document processing**: Handles text, PDFs, images, and other file types
- **Real-time chat interface**: Interactive conversations that combine knowledge, workflows, and automation
- And this is just the foundation. There is much more to come.

The platform helps users manage information, automate workflows, and get intelligent insights from their data.

## Contents
0) Core concepts in this codebase
1) Orientation: app, routers, and dependencies
2) Configuration and environment
3) Running the API locally
4) Authentication and authorization (how to get an auth token)
5) Response envelope standard
6) Frontend (React) — Overview and local development
7) Database and async ORM patterns
8) Chat flow end-to-end
9) RAG behavior (KB prompts, escalation)
10) Testing: integration test runner
11) Hands-on exercises (with auth)
12) Common Development Patterns & Troubleshooting
13) Future development plans
14) Additional Resources & Next Steps
15) Glossary: Plain-English Definitions + Links

---

## 0) Core concepts in this codebase

FastAPI
- A “router” groups related endpoints. You declare a router with APIRouter and then include it in the app. Decorators like @router.get("/items") map HTTP methods+paths to Python functions.
- “Dependencies” are functions FastAPI calls to supply parameters (e.g., DB sessions, settings). Declared with Depends.
- “Response models” are Pydantic models that FastAPI uses for output validation and OpenAPI docs.

Pydantic
- BaseModel: defines request/response schemas with types and validation.
- Settings (Pydantic Settings): loads environment variables into a typed object; we use load_dotenv(override=True) so .env wins.

Async SQLAlchemy
- AsyncSession: database session used in async endpoints.
- Relationship loading: use selectinload to fetch related rows up front; do not touch unloaded relations inside async request handlers (avoids MissingGreenlet errors).

Starlette/Responses
- JSONResponse: structured JSON output we use for response envelopes.
- StreamingResponse: streams tokens/chunks for LLM responses.

RBAC and Auth
- Two auth modes: Bearer JWT and ApiKey. AuthenticationMiddleware enforces global auth; RBAC is enforced via endpoint-level dependencies in src/shu/auth/rbac.py. Public endpoints are whitelisted in AuthenticationMiddleware.

### AI/RAG Concepts

**RAG (Retrieval-Augmented Generation):**
RAG is an AI technique that improves language model responses by first searching for relevant information in a knowledge base, then using that information to generate better, more accurate answers.

**How RAG Works:**
1. **User asks a question**: "What's our company's vacation policy?"
2. **System searches knowledge base**: Finds relevant documents about vacation policies
3. **System provides context to AI**: Gives the AI the relevant policy documents
4. **AI generates answer**: Uses both its training and the retrieved documents to answer

**Knowledge Base (KB):**
A collection of documents, files, and information that the AI can search through. In Shu, users can have multiple knowledge bases for different topics or projects.

**Embeddings:**
Mathematical representations of text that capture semantic meaning. Similar concepts have similar embeddings, allowing the system to find relevant information even when exact words don't match.

**LLM (Large Language Model):**
AI models like GPT-4, Claude, or Llama that can understand and generate human-like text. Shu supports multiple LLM providers.

### Middleware Concepts

**Middleware** is code that runs between receiving a request and sending a response. It's like a filter that can modify requests or responses.

**Common Middleware in Shu:**
- **AuthenticationMiddleware**: Global auth enforcement; public paths are listed in src/shu/core/middleware.py
- **RequestIDMiddleware & TimingMiddleware**: Request IDs and timing headers/logging
- **CORS Middleware**: Handles cross-origin requests from web browsers

### References in code
- FastAPI app and router wiring: src/shu/main.py
- Envelopes: src/shu/core/response.py and src/shu/schemas/envelope.py
- Middleware and auth: src/shu/core/middleware.py, src/shu/api/auth.py
- Async ORM usage: src/shu/services/chat_service.py

---

## 1) Orientation: app, routers, and dependencies

- App entrypoint: src/shu/main.py
  - create_app() constructs the FastAPI app, wires middleware, exception handlers, and includes routers.
  - A “router” here is an APIRouter instance that collects endpoints under a prefix and tags; then setup_routes(app) calls include_router(router).

Snippet
```python
# src/shu/main.py
def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    setup_middleware(app); setup_exception_handlers(app); setup_routes(app)
    return app
```

Dependencies
- You’ll see function parameters like db: AsyncSession = Depends(get_db). FastAPI resolves these before calling your endpoint.
- Config is injected with cfg: ConfigurationManager = Depends(get_config_manager_dependency).

Chat API location: src/shu/api/chat.py


### Architecture at a glance

- FastAPI routers and dependency injection
  - Routers live under src/shu/api/* and are included in src/shu/main.py
  - Common dependencies: `db: AsyncSession = Depends(get_db)`, `settings = Depends(get_settings_instance)`
  - See: src/shu/main.py, src/shu/api/*, src/shu/core/database.py

- Response envelopes (single format for success/error)
  - Use `ShuResponse.success(...)` and `ShuResponse.error(...)`
  - See: src/shu/core/response.py and docs/policies/API_RESPONSE_STANDARD.md

- Auth and RBAC
  - Global auth: AuthenticationMiddleware (JWT or ApiKey; public-path allowlist)
  - RBAC: endpoint-level dependencies (e.g., `get_current_user`, `require_admin`, `require_kb_access`)
  - See: src/shu/core/middleware.py, src/shu/auth/rbac.py

- Configuration (no hardcoded values)
  - Settings object from src/shu/core/config.py with `load_dotenv(override=True)`
  - Priority: .env > environment > code defaults
  - See: docs/policies/CONFIGURATION.md

- Async DB usage and eager-loading
  - Always use AsyncSession from `get_db`; avoid touching lazy relationships in request handlers
  - Eager-load relationships with `selectinload(...)` to prevent "greenlet_spawn has not been called" errors
  - See: src/shu/core/database.py and related services

- Streaming (SSE) contract
  - Chat supports streaming with `Accept: text/event-stream`; end marker `[DONE]`
  - See: src/shu/api/chat.py and tests/test_chat_streaming_*.py (if present)

- Frontend API client conventions
  - Axios interceptors add `Authorization: Bearer <token>`; auto-refresh on 401; envelope unwrapping helpers
  - See: frontend/src/services/api.js

Small DI example:
```python
@router.get("/kbs/{kb_id}")
async def get_kb(
    kb_id: UUID,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    kb = await kb_service.get_by_id(db, kb_id, user_id=user.id)
    return ShuResponse.success(kb)
```

---

## 2) Configuration and environment

### Understanding Configuration in Shu

Shu uses a **hierarchical configuration system** that loads settings from multiple sources in priority order:

1. **Environment Variables** (highest priority)
2. **`.env` file** (overrides defaults)
3. **Code defaults** (lowest priority)

### Pydantic Settings Explained

**Pydantic Settings** is a way to automatically load configuration from environment variables into typed Python objects.

```python
# src/shu/core/config.py
from pydantic import BaseSettings, Field
from dotenv import load_dotenv

# Load .env file (override=True means .env wins over existing env vars)
load_dotenv(override=True)

class Settings(BaseSettings):
    # Maps SHU_DATABASE_URL env var to database_url attribute
    database_url: str = Field(alias="SHU_DATABASE_URL")

    # Uses SHU_API_PORT or defaults to 8000
    api_port: int = Field(default=8000, alias="SHU_API_PORT")

    # String with default value
    api_v1_prefix: str = "/api/v1"
```

### ConfigurationManager

The **ConfigurationManager** handles complex configuration logic like user preferences, knowledge base settings, and model configurations.

```python
# Dependency injection pattern (preferred)
from shu.core.config import ConfigurationManager, get_config_manager_dependency

async def my_endpoint(
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    # Get RAG config (sync methods)
    rag_config = config_manager.get_rag_config_dict(
        model_config={"search_threshold": 0.65}
    )
    threshold = rag_config["search_threshold"]

    # Or fetch individual values
    max_results = config_manager.get_rag_max_results(model_config={"max_results": 8})
```

### Environment Setup Steps

**1. Copy the example environment file:**
```bash
cp .env.example .env
```

**2. Edit `.env` with your settings:**
```bash
# Database connection (required) — must use async driver
SHU_DATABASE_URL=postgresql+asyncpg://username:password@localhost:5432/shu

# API settings
SHU_API_PORT=8000
SHU_LOG_LEVEL=DEBUG

# LLM Provider API keys (optional)
OPENAI_API_KEY=your_openai_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
```

**3. Verify configuration works:**
```python
# Test in Python REPL
from shu.core.config import get_settings_instance
settings = get_settings_instance()
print(f"API prefix: {settings.api_v1_prefix}")
print(f"Database configured: {bool(settings.database_url)}")
```

### Configuration Best Practices

**DO:**
- Use dependency injection for ConfigurationManager
- Load settings from environment variables
- Use `.env` files for local development

**DON'T:**
- Hardcode configuration values in your code
- Create ConfigurationManager instances directly
- Put sensitive data (API keys, passwords) in code

---

## 3) Running the API locally

Shu’s backend is Python (FastAPI) and runs under an ASGI server (Uvicorn). We recommend using a Python virtual environment so project dependencies don’t pollute your global Python.

### A) Create and activate a virtual environment

- macOS/Linux
```bash
python3 --version  # Expect 3.11+ (3.12 is fine)
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

- Windows (PowerShell)
```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
```

### B) Install backend dependencies

```bash
pip install -r requirements.txt
```

If you see build errors, ensure your Python version matches what the team uses (3.11/3.12) and that your virtual environment is active.

### C) Ensure environment configuration

Make sure you have a .env file configured (see Section 2). Without required variables like SHU_DATABASE_URL, startup may fail or the API will report DB readiness problems.

### D) Start the API (Uvicorn)

Option 1 — Dev helper script (recommended):
```bash
python backend/scripts/run_dev.py
```

Option 2 — Run Uvicorn directly from repo root:
```bash
uvicorn shu.main:app --app-dir backend/src --reload --host 0.0.0.0 --port 8000
```

What this does:
- uvicorn: Starts the web server that runs your FastAPI app (an ASGI server)
- shu.main:app: Points to the FastAPI app object in backend/src/shu/main.py
- --app-dir backend/src: Adds backend/src to the import path so the `shu` package is importable
- --reload: Automatically restarts the server on code changes (development only)
- --host 0.0.0.0: Binds to all interfaces (useful in containers/VMs); for local-only, 127.0.0.1 is fine
- --port 8000: Serves on port 8000

What are Uvicorn and ASGI (in simple terms)?
- Uvicorn is the program that listens for web requests on a port and forwards them to your app.
- ASGI is the common “plug” standard that lets async Python apps (like FastAPI) and servers (like Uvicorn) talk to each other.

Learn more:
- Uvicorn: https://www.uvicorn.org/
- ASGI overview/spec: https://asgi.readthedocs.io/
- FastAPI: https://fastapi.tiangolo.com/

Optional logging for development (Linux/macOS):
```bash
SHU_LOG_LEVEL=DEBUG uvicorn shu.main:app --app-dir backend/src --reload --host 0.0.0.0 --port 8000
```
On Windows PowerShell:
```powershell
$env:SHU_LOG_LEVEL = "DEBUG"
uvicorn shu.main:app --app-dir backend/src --reload --host 0.0.0.0 --port 8000
```
Note: SHU_LOG_LEVEL controls Shu’s internal logging (via Settings), not Uvicorn’s own logger. To increase Uvicorn’s verbosity, add --log-level debug.

### E) Verify it’s running
- Visit http://localhost:8000/docs for interactive API docs
- GET http://localhost:8000/api/v1/health should return a 200

---

## 4) Authentication and authorization (how to get an auth token)

### Core Authentication Concepts

**Authentication** = "Who are you?" (proving identity)
**Authorization** = "What can you do?" (checking permissions)

**JWT (JSON Web Token):**
A secure way to transmit information between parties. Think of it like a digital ID card that contains your user information and expires after a certain time.

- **Bearer Token**: You include it in HTTP headers like: `Authorization: Bearer <your_token_here>`
- **Access Token**: Short-lived token (usually 15-60 minutes) for making API requests
- **Refresh Token**: Longer-lived token used to get new access tokens when they expire

**API Key Authentication:**
A simpler authentication method using a static key, mainly for automated systems.
- Header format: `Authorization: ApiKey <your_key_here>`

**RBAC (Role-Based Access Control):**
A security system where users have roles, and roles determine permissions.
- **admin**: Can do everything (manage users, system settings)
- **power_user**: Advanced features but can't manage other users
- **regular_user**: Standard user features

**Public Endpoints:**
Some API endpoints don't require authentication (like health checks, login pages). These are listed in the code at `src/shu/core/middleware.py`.

Auth endpoints (src/shu/api/auth.py)
- POST /api/v1/auth/login            { google_token }
- POST /api/v1/auth/google/exchange-login { code } (exchange Google auth code for tokens)
- POST /api/v1/auth/login/password   { email, password }
- POST /api/v1/auth/refresh          { refresh_token }
- GET  /api/v1/auth/me               → current user info
- Admin-only user management under /api/v1/auth/users

Ways to obtain dev credentials

A) Google OAuth (recommended if configured)
- Set Google client envs and perform the standard Google OAuth flow in the browser; exchange the returned `code` via POST /api/v1/auth/google/exchange-login with {"code":"…"} to receive access/refresh tokens.
- If you already have a Google ID token (bypassing the code exchange), POST /api/v1/auth/login with {"google_token":"…"} to receive access/refresh tokens.

B) Dev bootstrap admin (no Google)
- Create a one-off admin user directly in the DB, then mint a JWT. Example REPL snippet:
```python
# Dev-only: create admin user and token
import asyncio
from shu.core.database import get_db_session
from shu.auth.models import User, UserRole
from shu.auth.jwt_manager import JWTManager
async def go():
  db = await get_db_session()
  u = User(email="dev-admin@example.com", name="Dev Admin", role=UserRole.ADMIN.value,
           google_id="dev_bootstrap_admin", is_active=True)
  db.add(u); await db.commit(); await db.refresh(u)
  t = JWTManager().create_access_token({"user_id": u.id, "email": u.email, "role": u.role})
  print("ACCESS_TOKEN=", t)
asyncio.run(go())
```
- Use the printed token in Authorization: Bearer …. You now have admin rights for local testing.

C) Password users after bootstrap
- With an admin token, create an active password user:
```bash
curl -X POST http://localhost:8000/api/v1/auth/users \
 -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
 -d '{"email":"dev-user@example.com","password":"Password123!","name":"Dev User","role":"regular_user","auth_method":"password"}'
```
- Login to get a user token:
```bash
curl -X POST http://localhost:8000/api/v1/auth/login/password \
 -H "Content-Type: application/json" \
 -d '{"email":"dev-user@example.com","password":"Password123!"}'
```

D) ApiKey mode (optional)
- Set SHU_API_KEY and SHU_API_KEY_USER_EMAIL (must match an existing active user email). Then call APIs with Authorization: ApiKey <key>.

Known limitations (as of code inspection)
- Password self-registration POST /auth/register creates an inactive user requiring admin activation.
- User.google_id is declared non-nullable in src/shu/auth/models.py. Admin-created password users in /auth/users may fail without a google_id; if you hit DB errors, bootstrap via B) or ensure a google_id is set for the user row.

---

## 5) Response envelope standard

### Why Response Envelopes?
Shu uses a **consistent response format** for all API endpoints. This makes it easier for frontend applications to handle responses predictably.

**Success Response Format:**
```json
{
  "data": {
    // Your actual response data goes here
    "id": "123",
    "name": "Example",
    "created_at": "2025-01-21T10:30:00Z"
  }
}
```

**Error Response Format:**
```json
{
  "error": {
    "message": "Something went wrong",
    "code": "VALIDATION_ERROR",
    "details": {
      "field": "email",
      "reason": "Invalid email format"
    }
  }
}
```

### Using Response Helpers
Instead of manually creating JSON responses, use the helper functions:

```python
# src/shu/core/response.py
from shu.core.response import ShuResponse

# Success response (200)
return ShuResponse.success({"user_id": 123, "name": "John"})

# Created response (201)
return ShuResponse.created(new_user_data)

# Error response (400, 422, etc.)
return ShuResponse.error("Invalid input", code="VALIDATION_ERROR", status_code=422)
```

### Testing with Envelopes
When writing tests, use the helper to extract data from responses:

```python
# tests/response_utils.py
from tests.response_utils import extract_data

response = client.get("/api/v1/users/123")
user_data = extract_data(response)  # Unwraps {"data": {...}} to just {...}
assert user_data["name"] == "John"
```


### Frontend: Consuming envelope responses

In the React frontend, we keep response handling consistent:
- We use a helper to unwrap the envelope so components get plain data
- We format errors from the envelope structure so messages are user-friendly

Where this lives:
- Helper functions: frontend/src/services/api.js
  - extractDataFromResponse(response)
  - formatError(error)
- ESLint rule prevents using response.data.data directly (see frontend/package.json)

Example usage:
```javascript
import { extractDataFromResponse, formatError } from '../services/api';

try {
  const resp = await knowledgeBaseAPI.list();
  const items = extractDataFromResponse(resp); // safely unwraps { data: {...} }
  setKnowledgeBases(items);
} catch (e) {
  toast.error(formatError(e)); // handles { error: { message, code, details } }
}
```

Learn more:
- Axios: https://axios-http.com/
- React: https://react.dev/
---

## 6) Frontend (React) — Overview and local development

This section introduces the Shu Admin Console (the web UI) and how it talks to the backend.

### A) Tech stack at a glance
- React 18 (UI): https://react.dev/
- Create React App tooling (react-scripts)
- Material UI (components): https://mui.com/
- Axios (HTTP client): https://axios-http.com/
- React Router (navigation): https://reactrouter.com/
- React Query (data fetching/caching): https://tanstack.com/query/v3

See package.json for versions: frontend/package.json

### B) Run the frontend locally
```bash
cd frontend
npm install
npm start
```
- Opens http://localhost:3000
- Development proxy forwards API requests to http://localhost:8000 (see "proxy" in frontend/package.json)

Optional: set a specific API URL with a frontend .env file:
```bash
# frontend/.env
REACT_APP_API_BASE_URL=http://localhost:8000
```
If REACT_APP_API_BASE_URL is not set, the CRA dev proxy is used.

### C) How the frontend calls the API
- Central HTTP client lives in: frontend/src/services/api.js
- Adds Authorization: Bearer <token> if shu_token exists in localStorage
- Automatically handles 401 by attempting token refresh via /auth/refresh
- May refresh in the background if server sets header: X-Token-Refresh-Needed: true

Example (envelope-aware unwrapping and error formatting):
```javascript
import { knowledgeBaseAPI, extractDataFromResponse, formatError } from '../services/api';

try {
  const resp = await knowledgeBaseAPI.list();
  const items = extractDataFromResponse(resp);
  setRows(items);
} catch (e) {
  setError(formatError(e));
}
```

### D) Streaming chat in the frontend (SSE)
- Endpoint: POST /api/v1/chat/conversations/{id}/send with { stream: true }
- Client sends Accept: text/event-stream and reads SSE lines
- End-of-stream marker: data: [DONE]

Minimal usage (see chatAPI.streamMessage):
```javascript
const resp = await chatAPI.streamMessage(conversationId, { content: "Hello" });
const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buf = '';
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += decoder.decode(value, { stream: true });
  for (const line of buf.split('\n')) {
    if (!line.startsWith('data:')) continue;
    const payload = line.slice(5).trim();
    if (payload === '[DONE]') break;
    const { content } = JSON.parse(payload);
    appendToUI(content);
  }
}
```

### E) File uploads (chat attachments)
- Endpoint: POST /api/v1/chat/conversations/{id}/attachments
- Frontend uses multipart/form-data via FormData()
- See: chatAPI.uploadAttachment in frontend/src/services/api.js
- Allowed types are enforced by the backend; see backend configuration section

### F) Authentication in the browser
- Tokens are stored in localStorage as shu_token and shu_refresh_token
- On each request, Authorization header is set automatically by an Axios interceptor
- On 401, the client tries to refresh the token. If refresh fails, it clears tokens and redirects to /auth

Related files:
- frontend/src/services/api.js (interceptors, authAPI)
- frontend/src/components/ProtectedRoute.js and RoleBasedRoute.js

## 7) Database and async ORM patterns

### Understanding Async Database Operations

**Why Async?**
Shu uses asynchronous database operations to handle many requests simultaneously without blocking. This is crucial for a chat application where multiple users might be sending messages at the same time.

**AsyncSession vs Regular Session:**
- **AsyncSession**: Works with `async/await` syntax, doesn't block other operations
- **Regular Session**: Would block the entire application while waiting for database responses

### Critical Pattern: Eager Loading Relationships

**The Problem:**
In async code, you can't access database relationships after the database session is closed. This causes "MissingGreenlet" errors.

**The Solution:**
Use `selectinload()` to load related data upfront:

```python
# CORRECT: Load relationships when querying
from sqlalchemy.orm import selectinload

stmt = select(Conversation).where(Conversation.id == conversation_id).options(
    selectinload(Conversation.messages),           # Load all messages
    selectinload(Conversation.model_configuration), # Load model config
    selectinload(Conversation.user)                # Load user info
)
result = await db.execute(stmt)
conversation = result.scalar_one_or_none()

# Now you can safely access: conversation.messages, conversation.user, etc.
```

```python
# WRONG: This will cause errors in async code
conversation = await db.get(Conversation, conversation_id)
# This line will fail because messages weren't loaded:
message_count = len(conversation.messages)  # MissingGreenlet error (relationship not eagerly loaded)
```

### Data Transfer Objects (DTOs)

**Never return ORM objects directly from API endpoints.** Instead, convert them to Pydantic models:

```python
# CORRECT: Convert to Pydantic model
@router.get("/conversations/{id}")
async def get_conversation(id: str, db: AsyncSession = Depends(get_db)):
    # Get ORM object
    conversation = await get_conversation_with_relationships(db, id)

    # Convert to Pydantic DTO
    return ShuResponse.success(ConversationResponse.from_orm(conversation))

# WRONG: Return ORM object directly
return ShuResponse.success(conversation)  # Can cause serialization issues (raw ORM object)
```

### Common Database Patterns in Shu

```python
# Pattern 1: Create new record
async def create_user(db: AsyncSession, user_data: UserCreate):
    db_user = User(**user_data.dict())
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)  # Get the ID and other generated fields
    return db_user

# Pattern 2: Update existing record
async def update_user(db: AsyncSession, user_id: str, updates: UserUpdate):
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise ValueError("User not found")

    for field, value in updates.dict(exclude_unset=True).items():
        setattr(user, field, value)

    await db.commit()
    return user
```

---

## 8) Chat flow end-to-end

### Understanding the Chat System

The chat system is the core user interface for Shu. It handles conversations between users and AI assistants, with support for file attachments, streaming responses, and RAG integration.

**Key Components:**
- **API Layer**: `src/shu/api/chat.py` - HTTP endpoints for chat operations
- **Service Layer**: `src/shu/services/chat_service.py` - Business logic for chat operations
- **Models**: Database models for conversations, messages, attachments

### Chat Flow Steps

**1. Create a Conversation**
```bash
POST /api/v1/chat/conversations
{
  "title": "My Chat",
  "model_configuration_id": "uuid-here"
}
```

**2. Send a Message (Non-Streaming)**
```bash
POST /api/v1/chat/conversations/{conversation_id}/send
{
  "message": "What's our vacation policy?",
  "use_rag": true
}
```

**3. Stream a Response (Real-time)**
Streaming allows the AI response to appear word-by-word as it's generated, like ChatGPT.

```bash
GET /api/v1/chat/conversations/{conversation_id}/stream?message=Hello&use_rag=true
```

The response comes back as Server-Sent Events (SSE)-formatted lines:
```
data: {"content": "Hello"}
data: {"content": " there"}
data: {"content": "!"}
data: [DONE]
```

### File Attachments

**Upload Process:**
1. User uploads file: `POST /api/v1/chat/conversations/{id}/attachments`
2. System processes file using fast text extraction (no OCR for chat uploads)
3. Content is clipped (truncated if too long) based on settings
4. Content is injected as system context in the next message

Note: OCR is used elsewhere (e.g., knowledge base ingestion). For chat attachments, the current implementation uses fast extraction.

**Supported File Types (defaults in Settings):**
- PDFs (.pdf)
- Text files (.txt, .md)
- Word documents (.docx)

Configurable via SHU_CHAT_ATTACHMENT_ALLOWED_TYPES.

**Example Code:**
```python
# src/shu/api/chat.py
@router.post("/conversations", response_model=SuccessResponse[ConversationResponse])
async def create_conversation(
    conversation_data: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    conv = await chat_service.create_conversation(
        user_id=user.id,
        title=conversation_data.title,
        model_configuration_id=conversation_data.model_configuration_id
    )
    return ShuResponse.success(ConversationResponse.from_orm(conv))
```

---

## 9) RAG behavior (KB prompts, escalation)

### Understanding RAG in Shu

**When RAG Activates:**
RAG (Retrieval-Augmented Generation) only runs when:
1. A Knowledge Base (KB) is selected in the chat conversation, OR
2. The model configuration has a default KB set
3. The user's query passes certain filters (not too short, not just stop words like "hi", "thanks")

**The RAG Process:**
1. **Query Analysis**: System checks if the user's message needs knowledge base search
2. **Embedding Search**: Converts the query to mathematical vectors and searches for similar content
3. **Context Retrieval**: Finds the most relevant document chunks from the knowledge base
4. **Context Injection**: Adds the relevant information to the AI's context before generating a response

### Knowledge Base Prompts
**KB Prompt Overrides** let you customize how the AI behaves when using a specific knowledge base.

**Example:**
```
You are a helpful assistant for Acme Corp's HR policies.
Always be professional and cite specific policy sections when answering questions.
If you're unsure about a policy, direct users to contact HR directly.
```

This text gets prepended to every conversation that uses this knowledge base.

### Full-Document Escalation
Sometimes the AI needs more context than just small chunks. **Full-document escalation** provides the complete text of relevant documents.

**How it works:**
1. System identifies that chunks aren't providing enough context
2. Retrieves the full text of the most relevant documents
3. Injects complete documents as system messages (with size limits for performance)

**Code Example:**
```python
# src/shu/services/chat_service.py
if escalation.get("enabled"):
  for d in escalation.get("docs", []):
    if (content := d.get("content")):
      messages.append({
        "role": "system",
        "content": f"Full Document Context:\n{content}"
      })
```

### RAG Configuration Options
- **Search Threshold**: How similar content must be to be considered relevant (0.0-1.0)
- **Max Results**: Maximum number of document chunks to retrieve
- **Chunk Size**: How large each piece of retrieved text should be
- **Full-Doc Threshold**: When to escalate to full document retrieval

---

## 10) Testing: integration test runner

### Why Custom Testing Framework?

Shu uses a **custom integration test framework** instead of pytest because:
- **No async conflicts**: Pytest-asyncio had issues with our async database code
- **Real database testing**: Tests use actual PostgreSQL, not mocks
- **Faster execution**: Tests run much faster than with pytest
- **Better cleanup**: Automatic test data cleanup prevents database pollution

### Understanding Integration vs Unit Tests

**Integration Tests**: Test how different parts of the system work together
- Example: "Can I create a user, log in, create a conversation, and send a message?"
- Tests the full flow from HTTP request to database and back

**Unit Tests**: Test individual functions in isolation
- Example: "Does the password hashing function work correctly?"
- Tests one piece of logic at a time

### Running Tests

**List all available test suites:**
```bash
python -m tests.integ.run_all_integration_tests --list-suites
```

**Run all tests:**
```bash
python -m tests.integ.run_all_integration_tests
```

**Run specific test suite:**
```bash
python -m tests.integ.run_all_integration_tests --suite auth
python -m tests.integ.run_all_integration_tests --suite chat
```

**Run with detailed logging:**
```bash
python -m tests.integ.run_all_integration_tests --suite auth --log
```

### How the Test Framework Works

1. **Setup**: Creates a temporary admin user in the database
2. **Authentication**: Gets a JWT token for making authenticated requests
3. **Test Execution**: Runs each test with real HTTP requests to the API
4. **Cleanup**: Removes all test data from the database
5. **Reporting**: Shows which tests passed/failed with timing information

### Available Test Suites

- **auth**: Authentication and authorization tests
- **rbac**: Role-based access control tests
- **chat**: Chat conversation and messaging tests
- **config**: Configuration management tests
- **llm**: LLM provider management tests
- **knowledge_source**: Knowledge base and source tests

---

## 11) Hands-on exercises (with auth)

These exercises will help you understand how Shu works by making actual API calls. You'll create a conversation, send messages, and see how RAG works.

### Prerequisites

**1. Start the API server:**
```bash
uvicorn shu.main:app --reload --host 0.0.0.0 --port 8000
```

**2. Get authentication credentials:**
Follow section 4 (methods B or C) to get an access token, then:
```bash
export AUTH="Bearer <your_access_token_here>"
```

**3. Get a model configuration ID:**
```bash
# List available model configurations
curl -sS -H "Authorization: $AUTH" http://localhost:8000/api/v1/model-configurations | jq '.data[0].id'
```

### Exercise 1: Create a Conversation

**What this does:** Creates a new chat conversation that you can send messages to.

```bash
curl -sS -X POST http://localhost:8000/api/v1/chat/conversations \
 -H "Authorization: $AUTH" \
 -H "Content-Type: application/json" \
 -d '{"model_configuration_id":"<uuid_from_above>","title":"My First Conversation"}'
```

**Expected response:**
```json
{
  "data": {
    "id": "conversation-uuid-here",
    "title": "My First Conversation",
    "created_at": "2025-01-21T10:30:00Z",
    "user_id": "your-user-id",
    "model_configuration_id": "model-config-uuid"
  }
}
```

**Save the conversation ID for the next steps!**

### Exercise 2: Send a Simple Message (Non-Streaming)

**What this does:** Sends a message and gets the complete AI response at once.

```bash
curl -sS -X POST http://localhost:8000/api/v1/chat/conversations/<conversation_id>/send \
 -H "Authorization: $AUTH" \
 -H "Content-Type: application/json" \
 -d '{"message":"Hello! Can you introduce yourself?","use_rag":false}'
```

**Expected response:**
```json
{
  "data": {
    "user_message": {
      "id": "message-uuid",
      "content": "Hello! Can you introduce yourself?",
      "role": "user",
      "created_at": "2025-01-21T10:30:00Z"
    },
    "assistant_message": {
      "id": "message-uuid",
      "content": "Hello! I'm Shu, your AI assistant...",
      "role": "assistant",
      "created_at": "2025-01-21T10:30:01Z"
    }
  }
}
```

### Exercise 3: Stream a Response (Real-time)

**What this does:** Gets the AI response word-by-word as it's generated, like ChatGPT.

```bash
curl -sS -N "http://localhost:8000/api/v1/chat/conversations/<conversation_id>/stream?message=Tell%20me%20a%20short%20story&use_rag=false" \
 -H "Authorization: $AUTH"
```

**Expected response (streaming):**
```
data: {"content": "Once"}
data: {"content": " upon"}
data: {"content": " a"}
data: {"content": " time"}
...
data: [DONE]
```

### Exercise 4: Upload and Use a File Attachment

**What this does:** Uploads a file, processes it, and uses its content in the next message.

**4a. Create a test file:**
```bash
echo "This is a test document about Shu AI platform. It helps users manage information and automate workflows." > test_document.txt
```

**4b. Upload the file:**
```bash
curl -sS -X POST http://localhost:8000/api/v1/chat/conversations/<conversation_id>/attachments \
 -H "Authorization: $AUTH" \
 -F "file=@test_document.txt"
```

**4c. Ask about the file content:**
```bash
curl -sS -X POST http://localhost:8000/api/v1/chat/conversations/<conversation_id>/send \
 -H "Authorization: $AUTH" \
 -H "Content-Type: application/json" \
 -d '{"message":"What does the uploaded document say about Shu?","use_rag":false}'
```

The AI should reference the content from your uploaded file in its response!

### Exercise 5: Test RAG with Knowledge Base (if configured)

**What this does:** Uses RAG to search knowledge bases and provide informed answers.

```bash
curl -sS -X POST http://localhost:8000/api/v1/chat/conversations/<conversation_id>/send \
 -H "Authorization: $AUTH" \
 -H "Content-Type: application/json" \
 -d '{"message":"What are the main features of Shu?","use_rag":true}'
```

If you have knowledge bases configured, the AI will search them and provide more detailed, accurate answers based on your documents.

---

## 12) Common Development Patterns & Troubleshooting

### Common Patterns You'll See in Shu

**1. Dependency Injection Pattern**
```python
# Almost every endpoint uses this pattern
async def my_endpoint(
    db: AsyncSession = Depends(get_db),                    # Database session
    user: User = Depends(get_current_user),               # Current user
    config: ConfigurationManager = Depends(get_config_manager_dependency)  # Config
):
    # Your logic here
```

**2. Service Layer Pattern**
```python
# API endpoints delegate to service classes
@router.post("/users")
async def create_user(request: UserCreateRequest, db: AsyncSession = Depends(get_db)):
    # Don't put business logic here
    user = await user_service.create_user(db, request)
    return ShuResponse.success(UserResponse.from_orm(user))
```

**3. Response Envelope Pattern**
```python
# Always wrap responses
return ShuResponse.success(data)        # Success
return ShuResponse.error("message")     # Error
return ShuResponse.created(new_data)    # Created (201)
```

### Common Issues & Solutions

**Issue: "MissingGreenlet" errors**
```
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called
```
**Solution:** Use `selectinload()` to eager-load relationships:
```python
# WRONG
user = await db.get(User, user_id)
conversations = user.conversations  # Error: relationship not eagerly loaded

# CORRECT
stmt = select(User).where(User.id == user_id).options(
    selectinload(User.conversations)
)
user = await db.execute(stmt).scalar_one()
conversations = user.conversations  # Works after using selectinload
```

**Issue: "Object is not JSON serializable"**
**Solution:** Convert ORM objects to Pydantic models:
```python
# WRONG
return ShuResponse.success(db_user)  # SQLAlchemy ORM object (not JSON-serializable)

# CORRECT
return ShuResponse.success(UserResponse.from_orm(db_user))  # Pydantic model (JSON-serializable)
```

**Issue: Configuration not loading**
**Solution:** Check your `.env` file and environment variable names:
```python
# In Settings class, this maps SHU_DATABASE_URL to database_url
database_url: str = Field(alias="SHU_DATABASE_URL")
```

**Issue: Authentication errors**
**Solution:** Check your JWT token format:
```bash
# Correct format
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Wrong format (missing "Bearer ")
Authorization: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Development Workflow Tips

**1. Use the API documentation:**
- Visit `http://localhost:8000/docs` for interactive API docs
- Try endpoints directly in the browser

**2. Check logs for errors:**
```bash
# Run with debug logging
SHU_LOG_LEVEL=DEBUG uvicorn shu.main:app --reload
```

**3. Use the health endpoint:**
```bash
# Check if the API is running
curl http://localhost:8000/api/v1/health
```

**4. Test with the integration test framework:**
```bash
# Run tests to verify your changes work
python -m tests.integ.run_all_integration_tests --suite auth
```

---

## 13) Additional Resources & Next Steps

### Key Documentation to Read Next

**Development Standards:**
- `docs/policies/DEVELOPMENT_STANDARDS.md` - Coding standards and best practices
- `docs/policies/CONFIGURATION.md` - Detailed configuration guide
- `docs/policies/TESTING.md` - Testing framework documentation

**API Documentation:**
- `docs/policies/API_RESPONSE_STANDARD.md` - Response format standards
- Visit `/docs` when running the API for interactive documentation

**Architecture & Planning:**
- `docs/SHU_TECHNICAL_ROADMAP.md` - Current project priorities and tasks
- `docs/contracts/` - System contracts and interfaces
- `docs/flows/` - Workflow documentation


### Key Concepts to Master

**For Backend Development:**
- FastAPI dependency injection
- Async SQLAlchemy patterns
- Pydantic models and validation
- JWT authentication
- Response envelope patterns

**For AI/RAG Development:**
- Embedding generation and storage
- Vector similarity search
- Knowledge base management
- LLM integration patterns
- Prompt engineering

**For System Architecture:**
- Microservices patterns
- Event-driven architecture
- RBAC implementation
- Configuration management
- Testing strategies

### Getting Help

**Code Questions:**
- Check existing code patterns in similar files
- Look at test files for usage examples
- Use the interactive API docs at `/docs`

**Architecture Questions:**

## 14) Glossary: Plain-English Definitions + Links

- FastAPI: A Python framework for building web APIs quickly. Learn more: https://fastapi.tiangolo.com/
- ASGI: The standard that defines how async Python web servers and web apps communicate. Learn more: https://asgi.readthedocs.io/
- Uvicorn: A program (server) that runs ASGI apps like FastAPI and listens on a port (e.g., 8000). Learn more: https://www.uvicorn.org/
- Virtual Environment: An isolated Python environment so project packages don't affect your system Python. Learn more: https://docs.python.org/3/library/venv.html
- pip: Python's package installer (installs from PyPI). Learn more: https://pip.pypa.io/en/stable/
- .env (Environment Variables): A text file with KEY=VALUE settings loaded at startup (using python-dotenv). Learn more: https://github.com/theskumar/python-dotenv
- Pydantic: Library for defining and validating data structures (models) with types. Learn more: https://docs.pydantic.dev/latest/
- ORM (Object-Relational Mapper): Lets you work with database rows as Python objects. Learn more: https://en.wikipedia.org/wiki/Object%E2%80%93relational_mapping
- SQLAlchemy (Async): Python ORM with async support for database operations. Learn more: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- selectinload (eager loading): A way to pre-load related rows to avoid lazy-load errors in async contexts. Learn more: https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#selectin-eager-loading
- OpenAPI: A standard format for describing APIs; powers the /docs page. Learn more: https://swagger.io/specification/
- CORS: Browser security rules that control cross-site requests. Learn more: https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS
- JWT (JSON Web Token): A signed token that proves who you are (used for auth). Learn more: https://jwt.io/introduction
- RBAC (Role-Based Access Control): Permissions based on roles like admin/user/read-only. Learn more: https://en.wikipedia.org/wiki/Role-based_access_control
- SSE (Server-Sent Events): A way for servers to push text events to the browser over HTTP. Learn more: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- LLM (Large Language Model): AI models that generate text (e.g., GPT-4, Claude). Learn more: https://en.wikipedia.org/wiki/Large_language_model
- Embeddings: Number vectors that represent meaning of text for similarity search. Learn more: https://platform.openai.com/docs/guides/embeddings/what-are-embeddings
- RAG (Retrieval-Augmented Generation): Ask the model to read relevant docs first, then answer. Learn more: https://www.pinecone.io/learn/retrieval-augmented-generation/

- Review the contracts in `docs/contracts/`
- Check the flow documentation in `docs/flows/`


**Development Environment Issues:**
- Check `docs/policies/CONFIGURATION.md` and `.env.example` for setup help
- Verify your `.env` file configuration
- Test with the health endpoint: `/api/v1/health`



### G) Frontend resources to learn more
- React: https://react.dev/
- Material UI: https://mui.com/
- Axios: https://axios-http.com/
- React Router: https://reactrouter.com/
- React Query v3: https://tanstack.com/query/v3


### Appendix: Minimal Endpoint Recipe

When creating a new endpoint, use this template:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from shu.api.dependencies import get_db
from shu.core.config import get_config_manager_dependency, ConfigurationManager
from shu.core.response import ShuResponse
from shu.schemas.envelope import SuccessResponse

router = APIRouter(prefix="/example", tags=["example"])

@router.get("/ping", response_model=SuccessResponse[dict])
async def ping(
    db: AsyncSession = Depends(get_db),
    config: ConfigurationManager = Depends(get_config_manager_dependency)
):
    return ShuResponse.success({
        "pong": True,
        "environment": config.settings.environment
    })
```

**Welcome to the Shu development team!**
