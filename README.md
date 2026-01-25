# Shu

> **⚠️ Development Status**: Shu is in active development. The `main` branch may not be stable at any given commit. Use tagged releases for the most stable versions. A formal release process is coming soon.

**Shu** is an extensible AI knowledge platform for individuals, teams, and organizations. It combines a plugin ecosystem, knowledge base and retrieval, conversational interfaces, and background automation into a single platform. This repository contains the core backend API and React-based admin console. Shu currently supports multi-source retrieval-augmented generation (RAG) via its knowledge base and plugin feeds, but RAG is only one part of the broader system.

## Key Features

### Multi-Source Document Processing
- **Google Drive Integration**: Sync documents from Google Shared Drives
- **Filesystem Support**: Process local files and directories
- **Extensible Architecture**: Plugin system for additional source types
- **Smart Text Extraction**: Support for PDF, DOCX, Google Docs, and more

### Advanced RAG Capabilities
- **Vector Similarity Search**: Semantic search using sentence-transformers
- **Hybrid Search**: Combination of semantic and keyword search
- **Configurable Chunking**: Smart document chunking with overlap control
- **Multiple Knowledge Bases**: Multi-tenant support with isolated knowledge bases

### API
- **FastAPI Framework**: High-performance async API with automatic OpenAPI documentation
- **Background Job Processing**: Async sync operations with job tracking and management
- **Health Monitoring**: Health checks for Kubernetes deployment
- **Error Handling**: Error handling with detailed logging and standardized response envelopes

### React Admin Panel
- **Dashboard**: System overview with health monitoring and quick statistics
- **Knowledge Base Management**: Full CRUD operations for knowledge bases
- **Sync Job Monitoring**: Real-time monitoring and management of sync operations
- **Query Tester**: Interactive testing of vector similarity and hybrid search
- **Health Monitor**: Comprehensive system health monitoring

### Authentication & Security
- **Dual Authentication System**: Google OAuth + Password authentication
- **Secure Registration Model**: Self-registered users inactive by default, require admin activation
- **Role Enforcement**: Self-registered users forced to "regular_user" role (no privilege escalation)
- **JWT Token Management**: Secure access and refresh token handling
- **Role-Based Access Control (RBAC)**: Admin, Power User, Regular User, Read Only roles
- **Database-Driven Users**: All authentication data stored in PostgreSQL with bcrypt password hashing
- **Frontend Authorization**: Role-based UI component access control

### Integration & Scalability
- **OpenWebUI Filter**: Ready-to-use filter for OpenWebUI integration
- **Docker Support**: Complete containerization with Docker Compose
- **Database Migrations**: Alembic-based schema management
- **Multi-Frontend Ready**: API-first design supports multiple frontends and agents

## Architecture Overview

```mermaid
graph TD
    subgraph "Document Sources"
        A[Google Drive]
        B[Filesystem]
        C[Future Sources]
    end

    subgraph "Shu Backend"
        D[Sync Service]
        E[Query Service]
        F[Knowledge Base Management]
        G[(PostgreSQL + pgvector)]

        D --> A
        D --> B
        D --> C
        D --> G
        E --> G
        F --> G
    end

    subgraph "Frontends & Agents"
        H[React Admin Panel]
        I[OpenWebUI Filter]
        J[Custom Web Apps]
        K[AI Agents]

        H --> D
        H --> E
        H --> F
        I --> E
        J --> E
        K --> E
    end

    style D fill:#f39c12,stroke:#e67e22
    style E fill:#d5f5e3,stroke:#2ecc71
    style F fill:#d5f5e3,stroke:#2ecc71
    style G fill:#d5f5e3,stroke:#2ecc71
    style H fill:#eaf2f8,stroke:#3498db
```

## Quick Start

### Prerequisites

- **PostgreSQL** with pgvector extension
- **Redis** (required for progress tracking and caching)
- **Docker** and Docker Compose (recommended)
- **Python 3.11+** (for local development)
- **Node.js 16+** (for frontend development)

### Redis Setup

Shu requires Redis for progress tracking and caching. Choose one of the following options:

#### Option A: macOS (Homebrew)
```bash
# Install Redis
brew install redis

# Start Redis service
brew services start redis

# Verify Redis is running
redis-cli ping  # Should return "PONG"
```

#### Option B: Docker
```bash
# Run Redis in Docker
docker run -d --name shu-redis -p 6379:6379 redis:alpine

# Verify Redis is running
docker exec shu-redis redis-cli ping  # Should return "PONG"
```

#### Option C: Linux (Ubuntu/Debian)
```bash
# Install Redis
sudo apt update
sudo apt install redis-server

# Start Redis service
sudo systemctl start redis-server
sudo systemctl enable redis-server

# Verify Redis is running
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
   make up-api
   # or
   docker compose -f deployment/compose/docker-compose.yml up -d
   ```

   To also run the dev API with auto-reload on port 8001, use `make up-api-dev` or `make up-full-dev`.

4. **Verify the system**:

   ```bash
   # Backend health
   curl http://localhost:8000/api/v1/health/liveness

   # Frontend (if started)
   open http://localhost:3000
   ```

5. **Stop the stack:**

   ```bash
   make down        # normal shutdown
   make force-down  # aggressive shutdown; also removes shu-frontend if present
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
   export SHU_REDIS_URL=redis://localhost:6379
   export PYTHONPATH=$(pwd)/backend/src
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

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json

### Core API Endpoints

```
/api/v1/
├── health/                     # Health checks and monitoring
├── knowledge-bases/            # Knowledge base management (CRUD)
├── source-types/              # Available source types and validation
├── sync/                      # Document synchronization and job management
├── query/                     # Search and retrieval operations
├── experiences/               # Experience workflow management (CRUD and execution)
└── model-configurations/      # Model configuration management
```

## React Admin Panel

The Shu React Admin Panel provides a web interface for managing the system:

### Features
- **Dashboard**: System overview with health monitoring and quick statistics
- **Knowledge Base Management**: Create, edit, and delete knowledge bases
- **Experience Management**: Create, configure, and execute workflow experiences
- **Sync Job Monitoring**: Real-time monitoring and management of sync operations
- **Query Tester**: Interactive testing of vector similarity and hybrid search
- **Health Monitor**: System health monitoring

### Access
- **Development**: http://localhost:3000
- **Production**: Configured via environment variables

For detailed frontend setup and usage, see [frontend/README.md](./frontend/README.md).

## Configuration

Shu uses environment variables for configuration. Key settings include:

### Backend Configuration
```bash
# Database
SHU_DATABASE_URL=postgresql://user:password@host:5432/database

# Redis (Required)
SHU_REDIS_URL=redis://localhost:6379
SHU_REDIS_CONNECTION_TIMEOUT=5
SHU_REDIS_SOCKET_TIMEOUT=5

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
REACT_APP_ENVIRONMENT=development
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

If you have existing OAuth credentials, encrypt them after setting up the encryption key:

```bash
# Dry run to see what would be encrypted
python scripts/encrypt_existing_oauth_tokens.py --dry-run

# Perform the actual encryption
python scripts/encrypt_existing_oauth_tokens.py
```

## Testing

See the testing guide for current commands and scope:

- docs/policies/TESTING.md

Typical local runs:
```bash
# Backend integration tests
python -m tests.integ.run_all_integration_tests

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
  -d '{"query": "test query", "search_type": "hybrid"}'
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
        "search_type": "hybrid",
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
2. **Run tests**: `python -m tests.integ.run_all_integration_tests`
3. **Update database schema**: `python -m alembic revision --autogenerate -m "description"`
4. **Apply migrations**: `python -m alembic upgrade head`
5. **Frontend development**: `cd frontend && npm start`


## Deployment

### Docker Deployment

```bash
# Backend only (API + Postgres + Redis)
docker compose -f deployment/compose/docker-compose.yml up -d

# Full stack (API + Postgres + Redis + frontend)
docker compose -f deployment/compose/docker-compose.yml --profile frontend up -d

# Scale the API service
docker compose -f deployment/compose/docker-compose.yml up -d --scale shu-api=3
```

### Kubernetes Deployment

Shu is designed for Kubernetes deployment with:
- Health checks for liveness and readiness probes
- Configurable scaling and resource limits
- Automated sync jobs via CronJobs
- Monitoring and logging integration


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
2. **Run Tests**: Ensure 100% test success rate
3. **Update Documentation**: Keep README and API docs current
4. **Follow Git Workflow**: Use feature branches and pull requests

## Support

- **API Documentation**: http://localhost:8000/docs
- **Health Checks**: http://localhost:8000/api/v1/health/liveness
- **React Admin Panel**: http://localhost:3000
- **Configuration Guide**: [CONFIGURATION.md](./docs/policies/CONFIGURATION.md)
- **Testing Guide**: [TESTING.md](./docs/policies/TESTING.md)

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
