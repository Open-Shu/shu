# Shu

> **⚠️ Development Status**: Shu is in active development. The `main` branch may not be stable at any given commit. Use tagged releases for the most stable versions. A formal release process is coming soon.

**Shu** is an extensible AI knowledge platform for individuals, teams, and organizations. It combines a plugin ecosystem, knowledge base and retrieval, conversational interfaces, and background automation into a single platform. This repository contains the core backend API and React-based admin console. Shu currently supports multi-source retrieval-augmented generation (RAG) via its knowledge base and plugin feeds, but RAG is only one part of the broader system.

See our website for more information: [https://www.openshu.ai](https://www.openshu.ai)

## Key Features

### Plugin Ecosystem

- **Extensible Plugin System**: Python plugins with manifest-based discovery and host capabilities
- **Plugin Feeds**: Scheduled background ingestion from external sources (Gmail, Google Drive, etc.)
- **Connected Accounts**: OAuth-based provider identity for secure external service access
- **Host Capabilities**: Plugins access KB ingestion, secrets, HTTP, caching via standardized APIs

### Knowledge Base & RAG

- **Multi-Source Ingestion**: Documents from plugins, file uploads, or direct API
- **Vector Similarity Search**: Semantic search using sentence-transformers
- **Hybrid Search**: Combination of semantic and keyword search
- **Document Profiling**: LLM-powered document and chunk profiling for enhanced retrieval
- **Multiple Knowledge Bases**: Multi-tenant support with isolated knowledge bases

### Chat & Conversations

- **Streaming Chat**: Real-time LLM responses with server-sent events
- **RAG Integration**: Automatic context retrieval from knowledge bases
- **Multi-Provider LLM Support**: OpenAI, Anthropic, Google Gemini, Azure, Ollama
- **Conversation Memory**: Persistent conversation history with cross-session context

### Experiences (Workflows)

- **Configurable Workflows**: Compose plugins, KB queries, and LLM synthesis into reusable experiences
- **Scheduled Execution**: Cron-based triggers for automated workflows
- **Step-Based Processing**: Multi-step workflows with data flow between steps

### Authentication & Security

- **Dual Authentication**: Google OAuth + password authentication
- **Role-Based Access Control**: Admin, Power User, Regular User, Read Only roles
- **JWT Token Management**: Secure access and refresh token handling
- **Provider Identity**: Secure OAuth token storage for external service connections

### API & Infrastructure

- **FastAPI Backend**: High-performance async API with OpenAPI documentation
- **Background Scheduler**: In-process job scheduling for plugin feeds and experiences
- **Docker & Kubernetes Ready**: Complete containerization with health checks
- **Database Migrations**: Alembic-based schema management

## Architecture Overview

```mermaid
graph TD
    subgraph "External Services"
        A[Gmail]
        B[Google Drive]
        C[Calendar]
        D[Other APIs]
    end

    subgraph "Shu Backend"
        subgraph "Plugin Layer"
            E[Plugin Registry]
            F[Plugin Executor]
            G[Feeds Scheduler]
        end

        subgraph "Core Services"
            H[Chat Service]
            I[Query Service]
            J[Experience Engine]
            K[Knowledge Base Service]
        end

        L[(PostgreSQL + pgvector)]
        M[(Redis - optional)]

        E --> F
        G --> F
        F --> A
        F --> B
        F --> C
        F --> D
        F --> K
        H --> I
        H --> L
        I --> L
        J --> F
        J --> I
        K --> L
        H -.-> M
        K -.-> M
    end

    subgraph "Clients"
        N[React Frontend]
        O[API Consumers]
    end

    N --> H
    N --> J
    N --> K
    O --> H
    O --> I
```

## Quick Start

### Prerequisites

- **PostgreSQL** with pgvector extension
- **Redis** (optional - enables distributed caching; in-memory fallback for single-node)
- **Docker** and Docker Compose (recommended)
- **Python 3.11+** (for local development)
- **Node.js 16+** (for frontend development)

### Optional: Redis Setup

Redis is optional but recommended for production deployments. When available, Shu uses Redis for distributed caching and job queues. Without Redis, Shu falls back to in-memory implementations suitable for single-node deployments.

```bash
# macOS
brew install redis && brew services start redis

# Docker
docker run -d --name shu-redis -p 6379:6379 redis:alpine

# Linux (Ubuntu/Debian)
sudo apt update && sudo apt install redis-server
sudo systemctl start redis-server

# Verify
redis-cli ping  # Should return "PONG"
```

### Option 1: Docker Compose (Recommended)

1. **Create or update your `.env` at the repository root**:

   ```bash
   # If an example file is present
   cp .env.example .env
   # Then edit .env to set SHU_* and provider keys
   ```

   The docker-compose file at `deployment/compose/docker-compose.yml` will load this `.env` automatically.

2. **Start the full stack (Postgres + Redis + API + frontend)**:

   Using the Makefile:

   ```bash
   make up-full
   ```

   Or directly with Docker Compose:

   ```bash
   docker compose -f deployment/compose/docker-compose.yml --profile frontend up -d
   ```

   This starts:
   - `shu-postgres` (pgvector-enabled Postgres with a persistent volume)
   - `shu-db-migrate` (one-off job that ensures pgvector and runs Alembic migrations)
   - `redis`
   - `shu-api`
   - `shu-frontend` (React admin panel on port 3000)

3. **Backend-only stack (API + Postgres + Redis, no frontend)**:

   ```bash
   make up
   # or
   docker compose -f deployment/compose/docker-compose.yml up -d
   ```

   To run the dev API with auto-reload on port 8000, use `make up-dev` (backend only) or `make up-full-dev`.

4. **Verify the system**:

   ```bash
   # Backend health
   curl http://localhost:8000/api/v1/health/liveness

   # Frontend (if started)
   open http://localhost:3000
   ```

5. **Stop the stack:**

   ```bash
   make down  # stop containers (docker compose down --remove-orphans)

   # Optional: also remove volumes (destructive to local DB data)
   docker compose -f deployment/compose/docker-compose.yml down -v --remove-orphans
   ```

### Option 2: Local Development

1. **Install backend dependencies**

- **(macOS/Linux):**

   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

- **(Windows):**

   ```powershell
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Create and set up the PostgreSQL database:**

    First, create the database:

    ```bash
    createdb shu
    ```

    Then run the database setup script:

    ```bash
    # Set your database URL for the setup script. database.py accepts either
    # `postgresql://` or `postgresql+asyncpg://` here and normalizes it for
    # psycopg2/Alembic.
    export SHU_DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/shu"

    # Run full setup (init-db.sql + migrations + requirements check)
    python backend/scripts/database.py setup

    # Or provide database URL directly
    python backend/scripts/database.py setup --database-url postgresql+asyncpg://user:password@localhost:5432/shu
    ```

    This script will:
   - Execute `init-db.sql` to create required PostgreSQL extensions (pgvector, uuid-ossp, etc.)
   - Configure database settings for optimal performance
   - Run Alembic migrations to create all tables
   - Verify the setup meets all requirements

    Note: `backend/scripts/database.py` accepts both `postgresql://` and
    `postgresql+asyncpg://` URLs for setup, but when running the Shu API
    itself you should keep `SHU_DATABASE_URL` using the async
    `postgresql+asyncpg://` scheme as shown in the next step.

   Other useful database commands:

   ```bash
   python backend/scripts/database.py migrate   # Run migrations only
   python backend/scripts/database.py check     # Check status and requirements
   python backend/scripts/database.py reset --force  # Reset database (destructive)
   ```

3. **Set environment variables:**

   ```bash
   export SHU_DATABASE_URL=postgresql+asyncpg://username:password@localhost:5432/shu
   export PYTHONPATH=$(pwd)/backend/src
   # Optional: export SHU_REDIS_URL=redis://localhost:6379  # For distributed caching
   ```

4. **Start the backend server:**

- Option 1 — Dev helper script (recommended):

    ```bash
    python backend/scripts/run_dev.py
    ```

- Option 2 — Run Uvicorn directly from repo root:

    ```bash
    uvicorn shu.main:app --app-dir backend/src --reload --host 0.0.0.0 --port 8000
    ```

5. **Start the frontend (in a new terminal):**

   ```bash
   cd frontend
   npm install
   npm start
   ```

## API Documentation

Once running, access the generated API documentation:

- **Swagger UI**: <http://localhost:8000/docs>
- **ReDoc**: <http://localhost:8000/redoc>
- **OpenAPI JSON**: <http://localhost:8000/openapi.json>

### Core API Endpoints

```
/api/v1/
├── auth/                       # Authentication (login, tokens, user management)
├── health/                     # Health checks and monitoring
├── chat/                       # Chat conversations with LLM + RAG
├── knowledge-bases/            # Knowledge base management (CRUD, documents)
├── query/                      # Direct search and retrieval operations
├── plugins/                    # Plugin management (registry, feeds, execution)
├── experiences/                # Workflow automation (CRUD and execution)
├── llm/                        # LLM provider configuration
└── model-configurations/       # Model configuration management
```

## React Admin Panel

The Shu React Admin Panel provides a web interface for managing the system:

### Features

- **Dashboard**: System overview with health monitoring and quick statistics
- **Knowledge Base Management**: Create, edit, and delete knowledge bases
- **Experience Management**: Create, configure, and execute workflow experiences
- **Plugin Feeds**: Configure scheduled feeds, trigger runs, and view recent executions (Admin → Feeds)
- **Query Tester**: Interactive testing of vector similarity and hybrid search
- **Health Monitor**: System health monitoring

### Access

- **Development**: <http://localhost:3000>
- **Production**: Configured via environment variables

For detailed frontend setup and usage, see [frontend/README.md](./frontend/README.md).

## Configuration

Shu uses environment variables for configuration. Key settings include:

### Backend Configuration

```bash
# Database
SHU_DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database

# Redis (Optional - enables distributed caching for multi-node deployments)
# SHU_REDIS_URL=redis://localhost:6379
# SHU_REDIS_CONNECTION_TIMEOUT=5
# SHU_REDIS_SOCKET_TIMEOUT=5

# API
SHU_API_HOST=0.0.0.0
SHU_API_PORT=8000
SHU_ENVIRONMENT=production

# Google Drive Integration
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json

# Embedding Configuration
SHU_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
SHU_EMBEDDING_DIMENSION=384

# LLM Integration (Required for chat features)
SHU_LLM_ENCRYPTION_KEY=your_fernet_key_here

# OAuth Integration (Required for Gmail/Google Drive)
SHU_OAUTH_ENCRYPTION_KEY=your_fernet_key_here
```

### Generate Encryption Keys

Shu requires encryption keys for secure storage of API keys and OAuth tokens:

```bash
# Generate LLM encryption key
python -c "
from cryptography.fernet import Fernet
key = Fernet.generate_key()
print('SHU_LLM_ENCRYPTION_KEY=' + key.decode())
"

# Generate OAuth encryption key (separate from LLM key)
python -c "
from cryptography.fernet import Fernet
key = Fernet.generate_key()
print('SHU_OAUTH_ENCRYPTION_KEY=' + key.decode())
"
```

Add both keys to your `.env` file or environment variables.

### Frontend Configuration

```bash
# React Admin Panel
# Optional: if API host differs from the frontend host; else same-origin is used
REACT_APP_API_BASE_URL=http://localhost:8000

# Optional: enable verbose console logging
REACT_APP_DEBUG=true
```

For complete configuration options, see [CONFIGURATION.md](./docs/policies/CONFIGURATION.md).

## LLM Provider Setup

Shu supports multiple LLM providers for chat and completion features. Configure providers through the admin API:

### Supported Providers

**OpenAI**

```bash
curl -X POST http://localhost:8000/api/v1/llm/providers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "OpenAI Production",
    "provider_type": "openai",
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "sk-your_key_here",
    "supports_streaming": true,
    "rate_limit_rpm": 60
  }'
```

**Anthropic Claude**

```bash
curl -X POST http://localhost:8000/api/v1/llm/providers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Anthropic Claude",
    "provider_type": "anthropic",
    "api_endpoint": "https://api.anthropic.com/v1",
    "api_key": "sk-ant-your_key_here"
  }'
```

**Local Ollama**

```bash
curl -X POST http://localhost:8000/api/v1/llm/providers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Local Ollama",
    "provider_type": "ollama",
    "api_endpoint": "http://localhost:11434/v1",
    "api_key": null
  }'
```

**Azure OpenAI**

```bash
curl -X POST http://localhost:8000/api/v1/llm/providers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Azure OpenAI",
    "provider_type": "azure",
    "api_endpoint": "https://your-resource.openai.azure.com/openai/deployments/your-deployment",
    "api_key": "your_azure_key_here"
  }'
```

### Model Configuration

After setting up providers, configure models for use with knowledge bases:

```bash
# Create model configuration
curl -X POST http://localhost:8000/api/v1/llm/providers/{provider_id}/models \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "gpt-4",
    "display_name": "GPT-4 (Latest)",
    "context_window": 8000,
    "max_output_tokens": 4000,
    "supports_streaming": true
  }'
```

### OAuth Token Migration

Shu stores OAuth tokens encrypted at rest.

If you have an existing database that contains *plaintext* OAuth tokens from earlier
development versions, those rows will not decrypt once `SHU_OAUTH_ENCRYPTION_KEY` is enabled.

Currently there is no bundled bulk migration script. The recommended approach is to
re-connect affected provider accounts so tokens are re-stored encrypted.

## Testing

See the testing guide for current commands and scope:

- docs/policies/TESTING.md

Typical local runs:

```bash
# Backend integration tests
cd backend/src && python -m tests.integ.run_all_integration_tests

# Backend unit tests
python -m pytest backend/src/tests/unit

# Frontend unit tests
cd frontend && npm test -- --watchAll=false
```

### Run Specific Test Categories

```bash
# Health checks
curl http://localhost:8000/api/v1/health/liveness

# Knowledge base operations
curl http://localhost:8000/api/v1/knowledge-bases

# Query operations
curl -X POST http://localhost:8000/api/v1/query/{kb_id}/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test query", "query_type": "hybrid"}'
```

For detailed testing procedures, see [TESTING.md](./docs/policies/TESTING.md).

## Integration Examples

### Custom Integration

```python
import requests

# Query Shu API
response = requests.post(
    "http://localhost:8000/api/v1/query/kb_id/search",
    json={
        "query": "your question",
        "query_type": "hybrid",
        "limit": 5
    }
)

results = response.json()
```

## Development

### Project Structure

```
backend/
├── src/shu/                   # Backend application package
│   ├── main.py                # FastAPI application entrypoint
│   ├── api/                   # API endpoints
│   ├── core/                  # Core utilities (config, database, logging)
│   ├── models/                # SQLAlchemy database models
│   ├── schemas/               # Pydantic request/response schemas
│   ├── services/              # Business logic layer
│   └── ...                    # Other backend modules

frontend/
├── src/
│   ├── components/            # React components
│   ├── services/              # API services
│   ├── App.js                 # Main application
│   └── index.js               # Entry point
├── public/                    # Static assets
└── package.json               # Dependencies
```

### Development Workflow

1. **Make changes to source code**
2. **Run tests**: `cd backend/src && python -m tests.integ.run_all_integration_tests`
3. **Update database schema**: `python -m alembic -c backend/alembic.ini revision --autogenerate -m "description"`
4. **Apply migrations**: `python -m alembic -c backend/alembic.ini upgrade head`
5. **Frontend development**: `cd frontend && npm start`

## Deployment

For comprehensive deployment documentation including worker modes, scaling patterns, and production configurations,
see **[DEPLOYMENT_GUIDE.md](./docs/DEPLOYMENT_GUIDE.md)**.

### Quick Start - Docker Deployment

```bash
# Backend only (API + Postgres + Redis, inline workers)
docker compose -f deployment/compose/docker-compose.yml up -d

# Full stack (API + Postgres + Redis + frontend)
docker compose -f deployment/compose/docker-compose.yml --profile frontend up -d

# Add dedicated workers (production pattern)
docker compose -f deployment/compose/docker-compose.yml --profile worker up -d

# Scale the API service
docker compose -f deployment/compose/docker-compose.yml up -d --scale shu-api=3
```

**Using Makefile shortcuts:**

```bash
make up              # Backend only
make up-full         # Full stack with frontend
make up-worker       # Add dedicated workers
make down            # Stop all services
```

### Kubernetes Deployment

Shu is designed for Kubernetes deployment with:

- Health checks for liveness and readiness probes
- Configurable scaling and resource limits
- Independent scaling of API servers and worker pools
- Monitoring and logging integration

See [DEPLOYMENT_GUIDE.md](./docs/DEPLOYMENT_GUIDE.md) for Kubernetes manifests and scaling strategies.

## License

Shu is dual-licensed:

- **Open Source:** under the [GNU General Public License v3.0](./LICENSE.GPL-3.0) (GPLv3).
- **Commercial:** under the [Shu Commercial License](./LICENSE.COMMERCIAL.md) for organizations that wish to:
  - integrate Shu into proprietary products or services, or
  - avoid GPLv3 copyleft obligations.

For commercial licensing terms and contact information, see [LICENSE.COMMERCIAL.md](./LICENSE.COMMERCIAL.md).

## Contributions

By contributing to Shu, you agree to the terms of the [Contributor License Agreement (CLA)](./CLA.md).

## Contributing

1. **Follow Development Standards**: See [DEVELOPMENT_STANDARDS.md](./docs/policies/DEVELOPMENT_STANDARDS.md)
2. **Set Up Linting**: Run `make setup-hooks` to install pre-commit hooks (see [LINTING_QUICKSTART.md](./LINTING_QUICKSTART.md))
3. **Run Linters**: Use `make lint-fix` before committing to auto-fix issues
4. **Run Tests**: Ensure 100% test success rate
5. **Update Documentation**: Keep README and API docs current
6. **Follow Git Workflow**: Use feature branches and pull requests

## Code Quality

Shu uses automated linting and formatting to maintain code quality:

- **Python**: Ruff (linter + formatter) + mypy (type checker)
- **Frontend**: ESLint + Prettier
- **Automation**: Pre-commit hooks + GitHub Actions CI
- **Coverage**: Automatic test coverage tracking (no enforcement)

**Quick Start:**

```bash
# One-time setup
make setup-hooks

# Before committing
make lint-fix

# Run tests with coverage
make test-cov
```

## Support

- **API Documentation**: <http://localhost:8000/docs>
- **Health Checks**: <http://localhost:8000/api/v1/health/liveness>
- **React Admin Panel**: <http://localhost:3000>
- **Deployment Guide**: [DEPLOYMENT_GUIDE.md](./docs/DEPLOYMENT_GUIDE.md)
- **Configuration Guide**: [CONFIGURATION.md](./docs/policies/CONFIGURATION.md)
- **Testing Guide**: [TESTING.md](./docs/policies/TESTING.md)
- **Linting Guide**: [LINTING_GUIDE.md](./docs/LINTING_GUIDE.md)

## Legal Notices

### Patents

Open Shu Technologies, LLC. has filed patent applications related to the technology used in this software.

- **For GPLv3 Users:** You are granted a patent license to use these inventions solely as part of this software, subject to the terms and conditions of the GNU General Public License v3.0.
- **For Commercial Users:** If you are using this software under the Commercial License, your patent rights are governed by that agreement.
- **No Other Rights:** Use of this software outside the terms of the applicable license may constitute patent infringement.

### Trademarks

"Shu" and "Open Shu" are trademarks of Open Shu Technologies, LLC.

---

**Shu** is an extensible AI operating system for people and teams, currently focused on plugins, knowledge retrieval, and workflow experimentation.
