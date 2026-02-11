# Shu Configuration Guide

This document provides configuration instructions for the Shu.

## Configuration Architecture

Shu uses a **hierarchical configuration system** that ensures proper separation of concerns and prevents configuration chaos.

### Configuration Priority Cascade

```
User Preferences → Model Config → Knowledge Base Config → Global Defaults
```

1. **User Preferences** (highest priority): Personal settings users can control
   - Memory settings (conversation memory depth, similarity threshold)
   - UI/UX preferences (theme, language, timezone)
   - Advanced user settings (JSON configuration)

2. **Model Configuration**: LLM-specific settings (admin-only)
   - Temperature, max tokens, timeout settings
   - Model-specific prompts and behavior

3. **Knowledge Base Configuration**: RAG-specific settings (admin-only)
   - Search thresholds, max results, search types
   - Context formatting, reference styles
   - KB-specific prompts and instructions

4. **Global Defaults** (lowest priority): System-wide fallback values
   - Defined in `config.py` and ConfigurationManager
   - Environment variable overrides available


### Version and Build Metadata

The application exposes version/build information via environment variables that are typically baked in at build-time by the Makefile and GitHub Actions. These are read by Settings and surfaced at `/api/v1/system/version` and in readiness checks.

- SHU_APP_VERSION: SemVer of the build (e.g., 1.2.3). Defaults to 0.0.0-dev for local builds without tags.
- SHU_GIT_SHA: Short commit SHA for the build (e.g., abc1234). Defaults to unknown.
- SHU_BUILD_TIMESTAMP: UTC ISO8601 timestamp when the image was built.
- SHU_DB_RELEASE: Expected Alembic baseline that this image is built against (latest numeric squashed revision like 002). Readiness fails (503) if runtime DB alembic_version does not match this value.

Notes:
- These values should not be set manually in normal workflows; they are supplied by the build.
- For local builds, Makefile derives a reasonable VERSION from git tags or falls back to dev format, and detects DB_RELEASE from alembic/versions.


## OCR Processing Policy (Documents & Attachments)

Limitations/Known Issues:
- OCR quality varies by engine; EasyOCR with Tesseract fallback is used; noisy scans may yield little text
- OCR is compute‑heavy; progress callbacks are optional but PDFs are routed through OCR logic regardless

Policy:
- PDFs: Always treat as OCR candidates; do not try to guess if OCR is needed
- Text‑based files (docx, txt, html, etc.): Use fast text extraction only; do not OCR by default
- Empty content handling: allowed only for OCR/PDF paths; text‑based files with empty extraction are skipped
- Frontend: attachment upload errors must be surfaced to the user (inline alert/toast)

Configuration:
- Source ocr_mode: 'always' | 'auto' | 'fallback' | 'never' (default 'auto' with PDFs forced through OCR path)
- Global defaults live in config.py and ConfigurationManager; API endpoints should inject config via get_config_manager_dependency

- `SHU_OCR_EXECUTION_MODE`: OCR/Text extraction execution mode. Allowed: `thread` (in-process, default) | `process` (separate process for isolation). Scope: OCR only; does not affect embeddings.

Auditing/Logging:
- Log extraction method, engine, and durations; do not log raw content
- On failure, store error details in extraction_metadata but do not fail uploads

### ConfigurationManager

The `ConfigurationManager` is the **single source of truth** for all configuration resolution.

**Preferred: Dependency injection in API endpoints**
```python
from fastapi import Depends
from shu.core.config import get_config_manager_dependency, ConfigurationManager

async def some_endpoint(
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    # Get RAG configuration with proper cascade
    rag_config = await config_manager.get_rag_configuration(
        knowledge_base_id="kb-123",
        user_id="user-456"
    )

    # Get LLM configuration with proper cascade
    llm_config = await config_manager.get_llm_configuration(
        model_config_id="model-789",
        user_id="user-456"
    )

    # Get user preferences
    user_prefs = await config_manager.get_user_preferences("user-456")
```

**Preferred: Dependency injection in services**
```python
class SomeService:
    def __init__(self, db: AsyncSession, config_manager: ConfigurationManager):
        self.db = db
        self.config_manager = config_manager

    async def some_method(self):
        # Use injected config_manager
        config = await self.config_manager.get_rag_configuration(kb_id, user_id)
        return config

# Usage in API endpoint
async def endpoint(
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
    db: AsyncSession = Depends(get_db)
):
    service = SomeService(db, config_manager)
    return await service.some_method()
```

**LEGACY: Global instance (for backward compatibility only)**
```python
from shu.core.config import get_config_manager

# Only use this pattern for existing code that can't be refactored yet
config_manager = get_config_manager()
rag_config = await config_manager.get_rag_configuration(kb_id, user_id)
```

### Configuration Boundaries

**Users CAN configure:**
- Cross-session memory settings
- Memory depth and similarity thresholds
- UI theme, language, timezone
- Advanced personal settings (JSON)

**Users CANNOT configure:**
- RAG search thresholds or max results
- LLM temperature or max tokens
- System-level timeouts or limits
- Knowledge base or model settings

**Admins configure:**
- All RAG settings via Knowledge Base configuration
- All LLM settings via Model configuration
- System-wide defaults via environment variables

### Anti-Patterns: What NOT to Do

**NEVER hardcode configuration values in business logic:**

```python
# WRONG - Hardcoded values
threshold = 0.7
max_results = 10
temperature = 0.7

# WRONG - Hardcoded defaults in frontend
const [threshold, setThreshold] = useState(0.7);
```

**NEVER create ConfigurationManager instances directly:**

```python
# WRONG - Direct instantiation
config_manager = ConfigurationManager(settings)

# WRONG - Singleton pattern in new code
from shu.core.config import get_config_manager
config_manager = get_config_manager()  # Only for legacy compatibility
```

**ALWAYS use dependency injection for ConfigurationManager:**

```python
# CORRECT - Dependency injection in API endpoints
async def endpoint(
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    config = await config_manager.get_rag_configuration(kb_id, user_id)
    threshold = config.get("search_threshold")

# CORRECT - Dependency injection in services
class MyService:
    def __init__(self, db: AsyncSession, config_manager: ConfigurationManager):
        self.config_manager = config_manager
```

**ALWAYS fetch configuration from backend in frontend:**

```javascript
// CORRECT - Fetch from backend
const { data: config } = useQuery(['kb-config', kbId],
  () => knowledgeBaseAPI.getRAGConfig(kbId)
);
```

### Migration from Hardcoded Values

If you find hardcoded configuration values in the codebase:

1. **Identify the configuration type**: Is it user preference, model config, KB config, or system default?
2. **Use ConfigurationManager**: Replace hardcoded values with proper configuration calls
3. **Update frontend**: Fetch configuration from backend APIs instead of hardcoding
4. **Test the cascade**: Ensure the configuration priority cascade works correctly
5. **Update documentation**: Document any new configuration options

### Migration from Singleton Pattern to Dependency Injection

**Migrating API Endpoints:**

```python
# OLD: Singleton pattern
from ..core.config import get_config_manager

async def old_endpoint():
    config_manager = get_config_manager()  # Singleton (legacy pattern)
    config = await config_manager.get_rag_configuration(kb_id, user_id)

# NEW: Dependency injection
from fastapi import Depends
from ..core.config import get_config_manager_dependency, ConfigurationManager

async def new_endpoint(
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    config = await config_manager.get_rag_configuration(kb_id, user_id)  # via dependency injection
```

**Migrating Service Classes:**

```python
# OLD: Singleton in constructor
class OldService:
    def __init__(self, db: AsyncSession):
        self.db = db
        from ..core.config import get_config_manager
        self._config_manager = get_config_manager()  # Singleton (legacy pattern)

# NEW: Dependency injection
class NewService:
    def __init__(self, db: AsyncSession, config_manager: ConfigurationManager):
        self.db = db
        self.config_manager = config_manager  # via dependency injection

# Usage in endpoint:
async def endpoint(
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
    db: AsyncSession = Depends(get_db)
):
    service = NewService(db, config_manager)
```

**Migrating Service Instantiation:**

```python
# OLD: Service creates its own config manager
service = SomeService(db)  # Service internally uses get_config_manager()

# NEW: Pass config manager to service
service = SomeService(db, config_manager)  # Service receives config_manager
```

## Environment Variables

### Backend Configuration

#### Database Configuration
- `SHU_DATABASE_URL`: PostgreSQL connection string (default: `postgresql://shu_user:shu_password@localhost:5432/shu`)

#### API Configuration
- `SHU_API_HOST`: Host address for the API server (default: `0.0.0.0`)
- `SHU_API_PORT`: Port number for the API server (default: `8000`)
- `SHU_ENVIRONMENT`: Environment mode (`development`, `staging`, `production`)

### Plugins / Plugins Upload & Discovery

- Environment Variable: `SHU_PLUGINS_ROOT` (default: `./plugins`)
  - Root directory where plugin packages are installed and discovered
  - Relative paths are resolved from the repository root
  - Hot-reload: The Plugins Registry will refresh after successful upload; if refresh fails, the API will return `restart_required: true`



#### Logging Configuration
- `SHU_LOG_LEVEL`: Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
- `SHU_LOG_FORMAT`: Log format (`text` for human-readable, `json` for structured logging)

#### Security Configuration (Optional)
- `SHU_API_KEY`: Global API key for service-to-service access (Tier 0)
- `SHU_API_KEY_USER_EMAIL`: When using `SHU_API_KEY`, map requests to this user's email for RBAC context
- `SHU_SECRET_KEY`: Secret key for JWT tokens

#### Google Workspace Service Account (for Google Workspace plugins)
- `GOOGLE_SERVICE_ACCOUNT_JSON`: Path to Google service account JSON file

#### Embedding Configuration
- `SHU_EMBEDDING_MODEL`: Embedding model name (default: `sentence-transformers/all-MiniLM-L6-v2`)
- `SHU_EMBEDDING_DIMENSION`: Embedding dimension (default: `384`)
- `SHU_EMBEDDING_BATCH_SIZE`: Embedding batch size per encode call (default: `32`)
- `SHU_EMBEDDING_EXECUTION_MODE`: Execution mode for embeddings: `thread` (default, optimized) or `process`


#### Vector Database Configuration
- `SHU_VECTOR_INDEX_TYPE`: Vector index type (`ivfflat`, `hnsw`)
- `SHU_VECTOR_INDEX_LISTS`: Number of lists for IVFFlat index (default: `100`)

#### Chunking Configuration
- `SHU_DEFAULT_CHUNK_SIZE`: Default chunk size in characters (default: `1000`)
- `SHU_DEFAULT_CHUNK_OVERLAP`: Default chunk overlap in characters (default: `200`)

#### Cache Configuration
- `SHU_REDIS_URL`: Redis connection string for cache backend (optional)
  - **When set and Redis is reachable**: Uses RedisCacheBackend for all caching operations
  - **When set but Redis is unreachable**: Application startup or the first cache access will fail with `CacheConnectionError`. Treat this as a fatal misconfiguration that must be fixed rather than a mode switch.
  - **When not set**: Uses InMemoryCacheBackend for all caching operations (suitable for single-node and development deployments; no cross-process cache).
  - **Format**: `redis://localhost:6379/0` or `redis://user:password@host:port/db`
  - **Impact**: Affects plugin cache, rate limiting, configuration cache, and quota tracking
  - **Deployment flexibility**: Same application code works with or without Redis; to run without Redis, leave `SHU_REDIS_URL` unset.

#### Queue Configuration
- `SHU_REDIS_URL`: Redis connection string for queue backend (shared with cache)
  - **When set and Redis is reachable**: Uses RedisQueueBackend for all queue operations
  - **When set but Redis is unreachable**: Queue backend initialization fails with `QueueConnectionError`. Workers and schedulers will not process jobs until Redis is reachable.
  - **When not set**: Uses InMemoryQueueBackend for all queue operations (single-process only; no cross-process job queue).
  - **Impact**: Affects background job processing, document profiling, scheduled tasks
  - **Deployment flexibility**: Same application code works with or without Redis

- `SHU_WORKERS_ENABLED`: Enable background workers in this process (default: `true`)
  - **`true`** (default): Workers run in-process with the API server
    - Suitable for single-node deployments, development, and bare-metal installs
    - No additional processes needed - workers start automatically with the API
    - Uses InMemoryQueueBackend when Redis is not configured
  - **`false`**: Workers disabled in this process
    - Use when running separate dedicated worker processes
    - API process serves HTTP only (no background job processing)
    - Workers started separately via `python -m shu.worker`
    - Requires Redis for cross-process queue communication

- `SHU_WORKER_CONCURRENCY`: Number of concurrent worker tasks per process (default: `10`)
  - Controls how many async worker tasks run concurrently within a single process
  - Higher values improve throughput for I/O-bound jobs (LLM calls, API requests)
  - All workers share the same queue backend and compete for jobs
  - Applies to both inline workers (with API) and dedicated worker processes
  - Recommended: 8-16 for I/O-bound workloads, lower for CPU-bound work

##### Separate Worker Processes
When `SHU_WORKERS_ENABLED=false` on the API, start workers separately:

```bash
# Start a worker consuming all workload types
python -m shu.worker

# Start a worker with increased concurrency (4 concurrent tasks)
python -m shu.worker --concurrency 4

# Start a worker for specific workload types only
python -m shu.worker --workload-types INGESTION,PROFILING

# Start multiple specialized workers (in separate terminals/containers)
python -m shu.worker --workload-types INGESTION --concurrency 4
python -m shu.worker --workload-types LLM_WORKFLOW --concurrency 2
python -m shu.worker --workload-types MAINTENANCE,PROFILING
```

##### Horizontal Scaling Scenarios

**Scenario 1: Single-Node Development/Bare-Metal**
```bash
# No Redis needed, workers run in-process
SHU_WORKERS_ENABLED=true  # or unset (default)
# Start API - workers included automatically
python -m uvicorn shu.main:app --app-dir backend/src
```

**Scenario 2: Horizontally-Scaled Production**
```bash
# Redis required for cross-process communication. If Redis is down or unreachable,
# the queue/cache factories will raise connection errors rather than silently
# falling back to in-memory implementations.
SHU_REDIS_URL=redis://redis:6379/0
SHU_WORKERS_ENABLED=false  # Disable workers in API process

# Deploy API replicas (no workers)
# Container 1-N: API only
python -m uvicorn shu.main:app --app-dir backend/src

# Deploy specialized worker replicas
# Container A1-AN: Ingestion workers (scale based on document volume)
python -m shu.worker --workload-types INGESTION

# Container B1-BN: LLM workers (scale based on LLM request volume)
python -m shu.worker --workload-types LLM_WORKFLOW

# Container C1-CN: Maintenance workers (typically 1-2 replicas)
python -m shu.worker --workload-types MAINTENANCE,PROFILING
```

**Scenario 3: Mixed Workload Scaling**
```bash
# Scale ingestion workers independently from LLM workers
# Useful when document ingestion spikes don't correlate with chat usage

# Kubernetes example:
# - api: 3 replicas, SHU_WORKERS_ENABLED=false
# - worker-ingestion: 5 replicas, --workload-types INGESTION
# - worker-llm: 2 replicas, --workload-types LLM_WORKFLOW
# - worker-maintenance: 1 replica, --workload-types MAINTENANCE,PROFILING
```

##### WorkloadType Reference
- **INGESTION**: Document ingestion and indexing tasks (legacy, general ingestion)
- **INGESTION_OCR**: OCR/text extraction stage of document pipeline (first stage of async ingestion)
- **INGESTION_EMBED**: Embedding stage of document pipeline (chunking, embedding generation, vector storage)
- **LLM_WORKFLOW**: LLM-based workflows and chat processing
- **MAINTENANCE**: Scheduled tasks, cleanup, and system maintenance
- **PROFILING**: Document profiling (LLM-based analysis)

#### Sync Configuration
- `SHU_SYNC_TIMEOUT`: Default timeout for sync operations in seconds (default: `3600`)
- `SHU_SYNC_RETRY_ATTEMPTS`: Default number of retry attempts for failed documents (default: `3`)


#### Plugin Feeds Scheduler
- `SHU_PLUGINS_SCHEDULER_ENABLED`: Enable in-process plugin feeds scheduler (`true`/`false`, default: `true`)
- `SHU_PLUGINS_SCHEDULER_TICK_SECONDS`: Tick interval in seconds between scheduler passes (default: `60`)
- `SHU_PLUGINS_SCHEDULER_BATCH_LIMIT`: Max pending executions to claim/run per tick (default: `10`)

#### Sync Configuration Options
Sync configuration can be set at two levels:

**1. Source-Level Configuration (Recommended)**
Configure sync settings per source in the "Edit Source" dialog:
- `delete_missing`: Delete documents that no longer exist in source (default: `false`)
- `batch_size`: Number of documents to process in parallel (1-100, default: `10`)

**2. Sync-Time Configuration (Legacy)**
When creating or starting sync jobs, you can still configure:
- `sync_mode`: Sync mode - "optimized" (metadata-first filtering) or "forced" (full download) (default: "optimized")
- `timeout`: Timeout for sync operation in seconds (default: `3600`)
- `retry_attempts`: Number of retry attempts for failed documents (default: `3`)

**Forced Mode Options (only applicable when sync_mode = "forced"):**
- `skip_existing`: Skip documents that already exist (forced mode only, default: `false`)
- `force_update`: Force update all documents regardless of modification time (forced mode only, default: `false`)

**Configuration Priority:**
Source-level settings take precedence over sync-time settings. This allows you to:
- Set different deletion policies per source
- Optimize batch sizes based on source characteristics
- Configure once and forget (no need to set options every sync)

**Important:** The `delete_missing` option is powerful but potentially destructive. It will:
- Compare documents in the knowledge base against the current source
- Delete documents that are no longer found in the source
- Use proper database transactions to ensure data integrity
- Show what would be deleted in dry-run mode for safety

#### Host Capabilities (Plugins)
- `SHU_HTTP_EGRESS_ALLOWLIST`: Comma-separated list of allowed domains/hosts for plugin egress through host.http. Empty or unset allows all (development only). Example: `oauth2.googleapis.com,gmail.googleapis.com`
- `SHU_HTTP_DEFAULT_TIMEOUT`: Default timeout in seconds for host.http requests (default: `30`).

#### Auth Helpers
- `GOOGLE_DOMAIN` (optional): When set, host.auth.google_service_account_token will reject subjects not ending with `@<GOOGLE_DOMAIN>` (defense-in-depth for domain delegation).


### Frontend Configuration

#### React Admin Panel Configuration
- `REACT_APP_API_BASE_URL` (optional): Shu API base URL. If unset, the frontend uses same-origin. Example: `http://localhost:8000`
- `REACT_APP_ENVIRONMENT`: Environment mode (`development`, `staging`, `production`)
- `REACT_APP_VERSION`: Application version for display

#### Development Configuration
- `REACT_APP_DEBUG`: Enable debug mode (`true`/`false`)
- `REACT_APP_LOG_LEVEL`: Frontend logging level (`debug`, `info`, `warn`, `error`)




#### ModernChat (Sliding Window) Configuration — Frontend

Implementation Status: Complete

Purpose:
- Centralize all chat UI configuration in a single module and avoid scattering `process.env` reads throughout components.

Source of truth:
- File: `frontend/src/components/chat/ModernChat/utils/chatConfig.js`
- All ModernChat components must import constants from this file; do not read `process.env` directly in components.
- Environment variables are CRA build-time only (`REACT_APP_*`). Changes require rebuilding the frontend.

How values are resolved:
- Each constant is defined with an explicit parser and a code default.
- Precedence: environment override (if provided and valid) > code default.
- Zero values are allowed only where meaningful (e.g., overscan, pixel thresholds) via `parseNonNegativeInt`.

Example (excerpt):
- Defaults are defined in code; check the file for current values.

```javascript
// chatConfig.js (excerpt)
export const CHAT_WINDOW_SIZE = parsePositiveInt('REACT_APP_CHAT_WINDOW_SIZE', /* default */ 15);
export const CHAT_OVERSCAN = parseNonNegativeInt('REACT_APP_CHAT_OVERSCAN', /* default */ 5);
export const CHAT_SCROLL_TOP_THRESHOLD = parseNonNegativeInt('REACT_APP_CHAT_SCROLL_TOP_THRESHOLD_PX', 120);
export const CHAT_SCROLL_BOTTOM_THRESHOLD = parseNonNegativeInt('REACT_APP_CHAT_SCROLL_BOTTOM_THRESHOLD_PX', 32);
export const CHAT_PAGE_SIZE = parsePositiveInt('REACT_APP_CHAT_PAGE_SIZE', 50);
```

Semantics:
- `CHAT_WINDOW_SIZE`: number of messages in the primary window (non‑overscanned). Triggers (top/bottom reveal) are based on this window, not overscan.
- `CHAT_OVERSCAN`: small render buffer above/below the window. Improves perceived smoothness but does not change reveal triggers. 0–5 is typical.
- `CHAT_SCROLL_TOP_THRESHOLD_PX` / `CHAT_SCROLL_BOTTOM_THRESHOLD_PX`: pixel thresholds for when the list is considered at top/bottom.
- `CHAT_PAGE_SIZE`: server page size for message fetches.

Adding a new chat env:
1) Add a constant in `chatConfig.js` using the appropriate parser.
2) Update `frontend/.env.example` with a commented example and description.
3) Update this section and `docs/FRONTEND_OVERVIEW.md` (Environment & Configuration) as quick reference.
4) Import the new constant in components; do not reference `process.env` directly.

### Development Configuration

## Morning Briefing connectors (Gmail, Calendar, Google Chat) — Configuration

Implementation Status: Partial

Limitations/Known Issues:
- GmailDigest and CalendarDigest default to domain‑wide delegation; they require a service account JSON and Admin Console scopes. If a target `user_email` is not provided to plugins at runtime, those plugins will error.
- Google Chat participant resolution depends on the Admin SDK Directory API. Without it, messages will display sender/mention IDs (e.g., `users/123…`) and participant emails may be empty in the UI.
- Propagation of Admin Console scope changes can take minutes; transient "insufficient authentication scopes" errors are expected immediately after changes.

Prerequisites (Google Workspace):
- Follow docs/internal/domain-wide-delegation.md to:
  - Enable APIs: Gmail API, Google Chat API, Admin SDK, and (optionally) Google Calendar API
  - Create a service account with Domain‑Wide Delegation enabled
  - Authorize these scopes for the service account Client ID in Admin Console:
    - https://www.googleapis.com/auth/gmail.readonly
    - https://www.googleapis.com/auth/chat.messages.readonly
    - https://www.googleapis.com/auth/chat.spaces.readonly
    - https://www.googleapis.com/auth/admin.directory.user.readonly
    - https://www.googleapis.com/auth/calendar.readonly (for CalendarDigest)

Required environment variables:
- GOOGLE_SERVICE_ACCOUNT_JSON: absolute or relative path to the service account JSON
- GOOGLE_ADMIN_USER_EMAIL: admin user to impersonate when using the Admin Directory API (for GChat participant resolution)

Plugin references:
- Gmail: plugins/shu_gmail_digest (manifest.py, plugin.py)
- Calendar: plugins/shu_calendar_events (manifest.py, plugin.py)
- Google Chat: plugins/shu_gchat_digest (manifest.py, plugin.py)


Legacy Implementation Note:
- Legacy processors under `src/shu/processors/*` have been removed. The canonical path is Plugins + Plugin Feeds (Plugin Ecosystem v1). Author plugins against `docs/contracts/PLUGIN_CONTRACT.md` and surface schedules/feeds via Plugins Admin Feeds.

Per‑connector configuration model:
- GmailDigestPlugin (domain delegation by default)
  - Params: `user_email` (required for domain delegation), optional `since_hours` (default 48), `query_filter`, `max_results`
  - Uses GOOGLE_SERVICE_ACCOUNT_JSON; requires gmail.readonly scope
- CalendarDigestPlugin (domain delegation by default; personal OAuth supported)
  - Params: `auth_mode` (default `domain_delegation`), `user_email` (required in domain delegation mode), `calendar_id` (default `primary`), `window_hours` (default 24)
  - Uses GOOGLE_SERVICE_ACCOUNT_JSON; requires calendar.readonly scope
- GChatDigestPlugin (Admin SDK optional but recommended)
  - Resolves `sender_email` and `mentioned_users` via Admin SDK if GOOGLE_ADMIN_USER_EMAIL is set and Admin scope is authorized; otherwise falls back to unreadable Chat user IDs

Frontend behavior:
- Morning Briefing UI surfaces a deduplicated list of participant emails for Google Chat when available.
- If Admin SDK is not configured, the list may be empty; raw JSON always includes original Chat user IDs.

Verification checklist:
- .env.example includes GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_ADMIN_USER_EMAIL
- Domain‑wide delegation entry in Admin Console includes all scopes listed above
- Test a run for a known `user_email` to confirm Gmail/Calendar fetch works and Chat participants resolve to emails

- `SHU_DEBUG`: Enable debug mode (`true`/`false`)
- `SHU_RELOAD`: Enable auto-reload for development (`true`/`false`)

## Docker Configuration

### Docker Compose Development Stack (Recommended)

**Prerequisites:** Docker and Docker Compose v2; the stack includes PostgreSQL and Redis.

1. **Environment file:**
   ```bash
   # From repo root
   cp .env.example .env  # if present
   # Then edit .env to set SHU_* and provider keys
   ```

2. **(Optional) Build Docker images for the Compose stack:**
   ```bash
   make compose-build
   # Equivalent to: docker compose -f deployment/compose/docker-compose.yml --profile frontend build
   ```

3. **Backend-only stack (API + Postgres + Redis, no frontend):**
   ```bash
   make up
   # or
   docker compose -f deployment/compose/docker-compose.yml up -d
   ```

4. **Full stack (API + Postgres + Redis + frontend):**
   ```bash
   make up-full
   # or
   docker compose -f deployment/compose/docker-compose.yml --profile frontend up -d
   ```

5. **Backend-only stack with dev API (hot reload on port 8001):**
   ```bash
   make up-dev
   # or
   docker compose -f deployment/compose/docker-compose.yml --profile dev up -d
   ```

6. **Full stack with dev API (API + Postgres + Redis + frontend + dev API):**
   ```bash
   make up-full-dev
   # or
   docker compose -f deployment/compose/docker-compose.yml --profile dev --profile frontend up -d
   ```

7. **Optional reverse proxy (nginx):**
   ```bash
   docker compose -f deployment/compose/docker-compose.yml --profile proxy up -d nginx
   ```

8. **Stopping the stack:**
   ```bash
   make down        # normal shutdown
   make force-down  # aggressive shutdown; also removes shu-frontend if present
   ```

### Manual Docker Commands

1. **Build the image:**
   ```bash
   docker build -t shu-rag-backend .
   ```

2. **Run with external PostgreSQL:**
   ```bash
   # Ensure your external PostgreSQL has pgvector extension:
   # psql your_database -c "CREATE EXTENSION IF NOT EXISTS vector;"

   docker run -d --name shu-api \
     -e SHU_DATABASE_URL=postgresql://your_username:your_password@host.docker.internal:5432/shu \
     -p 8000:8000 \
     shu-rag-backend

   # For Linux, replace host.docker.internal with your actual IP or localhost
   # docker run -d --name shu-api \
   #   -e SHU_DATABASE_URL=postgresql://your_username:your_password@localhost:5432/shu \
   #   --network host \
   #   shu-rag-backend
   ```

## Development Setup

### 1. Backend Setup

#### Install Dependencies
```bash
pip install -r requirements.txt
```

#### Set Up Database
```bash
# Create PostgreSQL database with pgvector
createdb shu
psql shu -c "CREATE EXTENSION vector;"

# Run migrations
python -m alembic upgrade head
```

#### Configure Environment
Create a `.env` file in the project root:
```bash
SHU_DATABASE_URL=postgresql://your_user:your_password@localhost:5432/shu
SHU_LOG_LEVEL=DEBUG
SHU_ENVIRONMENT=development
```

#### Start the API Server
Option 1 — Dev helper script (adds the correct app dir):
```bash
python backend/scripts/run_dev.py
```

Option 2 — Run Uvicorn directly from repo root:
```bash
python -m uvicorn shu.main:app --app-dir backend/src --reload --port 8000
```

### 2. Frontend Setup

#### Install Dependencies
```bash
cd frontend
npm install
```

#### Configure Environment
Create a `.env` file in the frontend directory:
```bash
# Optional: set when API host differs from the frontend host; else same-origin is used
REACT_APP_API_BASE_URL=http://localhost:8000
REACT_APP_ENVIRONMENT=development
REACT_APP_DEBUG=true
```

#### Start the Development Server
```bash
npm start
```

The React admin panel will be available at `http://localhost:3000`.

### 3. Full-Stack Development

#### Using Docker Compose (Recommended)
```bash
# Start both backend and frontend
docker compose -f deployment/compose/docker-compose.yml --profile frontend up -d

# View logs
docker compose -f deployment/compose/docker-compose.yml logs -f shu-api
docker compose -f deployment/compose/docker-compose.yml logs -f shu-frontend
```

#### Manual Development
```bash
# Terminal 1: Start backend
python -m uvicorn shu.main:app --app-dir backend/src --reload --port 8000

# Terminal 2: Start frontend
cd frontend
npm start
```

## Production Deployment

### 1. Database Setup
- Use a managed PostgreSQL service (AWS RDS, Google Cloud SQL, etc.)
- Ensure pgvector extension is installed
- Set up proper backup and monitoring

### 2. Environment Configuration
- Use environment variables for sensitive configuration
- Set `SHU_ENVIRONMENT=production`
- Configure proper logging levels and outputs

### 3. Security Considerations
- Use HTTPS in production
- Set up API key authentication
- Configure CORS properly
- Use a reverse proxy (Nginx, Traefik, etc.)

### 4. Monitoring and Health Checks
- Use the `/api/v1/health` endpoint for health checks
- Monitor database performance and connection pools
- Set up alerts for API errors and performance issues

### 5. Frontend Production Build
```bash
cd frontend
npm run build
```

The build artifacts will be in the `frontend/build/` directory and can be served by a web server or CDN.


## Troubleshooting

### Common Issues

1. **Database Connection Errors:**
   - Check PostgreSQL is running and accessible
   - Verify database credentials
   - Ensure pgvector extension is installed

2. **API Errors:**
   - Check logs for detailed error messages
   - Verify environment variables are set correctly
   - Test database connectivity

3. **Frontend Connection Errors:**
   - If the API is on a different host, set `REACT_APP_API_BASE_URL` appropriately; otherwise same-origin is used
   - Check CORS configuration in the API when using cross-origin API hosts
   - Ensure the API server is running

4. **Performance Issues:**
   - Monitor database query performance
   - Check embedding model loading time
   - Verify vector index configuration

### Debugging

1. **Enable Debug Logging:**
   ```bash
   SHU_LOG_LEVEL=DEBUG
   ```

2. **Use Human-Readable Logs (Development):**
   ```bash
   SHU_LOG_FORMAT=text
   ```

3. **Use Structured Logs (Production):**
   ```bash
   SHU_LOG_FORMAT=json
   ```

4. **Check Health Endpoint:**
   ```bash
   curl http://localhost:8000/api/v1/health
   ```

5. **Database Diagnostics:**
   ```bash
   curl http://localhost:8000/api/v1/health/database
   ```

6. **Frontend Debug Mode:**
   ```bash
   REACT_APP_DEBUG=true
   ```

### Logging Features

The Shu logging system includes several improvements for better readability:

- **Color-coded log levels**: DEBUG (cyan), INFO (green), WARNING (yellow), ERROR (red), CRITICAL (magenta)
- **Proper alignment**: Timestamp, level, and logger name are aligned for easy scanning
- **Reduced noise**: SQL queries and verbose operations moved to DEBUG level
- **Human-readable format**: Default text format is easier to read than JSON
- **Structured logging**: JSON format available for production monitoring
- **Smart filtering**: Only meaningful extra fields are included in logs

## API Documentation

Once the server is running, access the interactive API documentation at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
