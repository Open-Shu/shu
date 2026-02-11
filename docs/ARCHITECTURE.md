# Shu Architecture: Extensible AI Knowledge Platform

**Last Updated**: 2025-01-25


## Quick System Index (for session hydration)

Use this section to locate live systems quickly and avoid reinventing them. Each entry includes Implementation Status, primary code paths, and canonical docs.

- Plugin System v1
  - Executor: src/shu/plugins/executor.py
  - Host Capabilities: src/shu/plugins/host/ (host_builder.py, kb_capability.py, auth_capability.py)
  - Registry: src/shu/plugins/registry.py
  - Feeds Scheduler Service: src/shu/services/plugins_scheduler_service.py
  - Admin/Public API (plugins/feeds/executions/secrets): src/shu/api/plugins_router.py (+ sub-routers: plugins_public.py, plugins_admin.py, plugins_feeds.py, plugins_executions.py, plugin_secrets.py)
  - Models: src/shu/models/plugin_feed.py, src/shu/models/plugin_execution.py, src/shu/models/plugin_registry.py
  - Reference Plugins: plugins/shu_gdrive_files, plugins/shu_gmail_digest
  - Contract Docs: docs/contracts/PLUGIN_CONTRACT.md


- Knowledge Base & Ingestion
  - Services: src/shu/services/document_service.py, src/shu/services/knowledge_object_service.py
  - Models: src/shu/models/document.py
  - Host Ingestion APIs (emails/docs/threads/text) via host.kb: src/shu/plugins/host/kb_capability.py; policy described in docs/contracts/PLUGIN_CONTRACT.md


- Auth & Identity
  - Host Auth (provider-agnostic): src/shu/api/host_auth.py
  - Provider identity model: src/shu/models/provider_identity.py
  - Plugin auth resolution: src/shu/services/plugin_identity.py
  - Settings: src/shu/core/config.py (SHU_OAUTH_ENCRYPTION_KEY, etc.)


- Cache System
  - Unified Interface: src/shu/core/cache_backend.py (CacheBackend protocol, get_cache_backend factory)
  - Redis Backend: RedisCacheBackend (production/multi-node deployments)
  - In-Memory Backend: InMemoryCacheBackend (development/single-node)
  - Backend Selection: Automatic based on SHU_REDIS_URL configuration
  - Usage: Plugin cache, rate limiting, configuration cache, quota tracking
  - Standards: docs/policies/DEVELOPMENT_STANDARDS.md Section 25


- Queue System
  - Unified Interface: src/shu/core/queue_backend.py (QueueBackend protocol, Job dataclass, get_queue_backend factory)
  - Redis Backend: RedisQueueBackend (production/multi-node deployments with competing consumers)
  - In-Memory Backend: InMemoryQueueBackend (development/single-node)
  - Workload Routing: src/shu/core/workload_routing.py (WorkloadType enum, enqueue_job helper)
  - Worker: src/shu/core/worker.py (Worker class, WorkerConfig)
  - Worker Entrypoint: src/shu/worker.py (dedicated worker process)
  - Backend Selection: Automatic based on SHU_REDIS_URL configuration
  - Worker Mode: SHU_WORKERS_ENABLED (true=inline with API, false=use dedicated worker processes)
  - Usage: Background job processing, document profiling, scheduled tasks
  - Standards: docs/policies/DEVELOPMENT_STANDARDS.md Section 26


- API Routers (FastAPI)
  - Aggregation: src/shu/main.py (includes routers; see app.include_router calls)
  - Plugins/Feeds/Executions: src/shu/api/plugins.py
  - Other routers: src/shu/api/{knowledge_bases.py, query.py, prompts.py, chat.py, ...}

- Background Scheduler
  - Service: src/shu/services/plugins_scheduler_service.py (enqueue/run; in-process)
  - Config toggles: src/shu/core/config.py (SHU_PLUGINS_SCHEDULER_ENABLED, tick seconds, batch limit)


- Frontend (React)
  - Root: ./frontend (see docs/FRONTEND_OVERVIEW.md)
  - Admin pages: /admin/plugins, /admin/feeds, Connected Accounts
  - API client: frontend/src/services/{pluginsApi.js, schedulesApi.js}
  - Env: VITE_API_BASE_URL (optional)

- Chat System
  - Routers: src/shu/api/chat.py (+ chat_plugins gated in main)


- Workflow Engine
  - Contract: docs/contracts/WORKFLOW_CONTRACT.md

- Configuration
  - Docs: docs/policies/CONFIGURATION.md
  - Settings object: src/shu/core/config.py (get_settings_instance)

- Testing
  - Docs: docs/policies/TESTING.md, tests/README.md
  - Integration tests: tests/*

Cross-References
- Project plan and roadmap: docs/SHU_TECHNICAL_ROADMAP.md


## Vision

Shu is an extensible AI operating system for work that transforms how knowledge workers interact with information and manage their professional responsibilities. The platform combines retrieval over knowledge bases with agentic workflows to provide proactive assistance, contextual insights, and automated processes.

## Core Architecture Principles

### 1. **Extend, Don't Replace (Guideline for DRY and reuse)**
- Prefer reusing existing systems and contracts before adding new ones
- When a new system supersedes an old one (e.g., Plugin Feeds replacing Knowledge Base Sources), deprecate quickly and remove legacy paths after parity
- This principle is aimed at developers and AI assistants to prevent parallel, duplicated systems; it is not an absolute rule

### 2. **Plugin-First Architecture**
- Convert existing integrations (Gmail, Calendar) to standardized plugin interfaces
- Enable agents to interact with external services through unified contracts
- Support extensible plugin ecosystem for future capabilities

### 3. **Workflow-Driven Intelligence**
- Multi-step processes with human approval gates
- Declarative workflow templates for common patterns
- Event-driven execution with proper error handling

### 4. **Memory-Aware Context**
- Persistent user profiles and organizational intelligence
- Conversation memory with privacy boundaries
- Behavioral pattern analysis for personalized assistance

### 5. **Privacy-First Design**
- RBAC enforcement for all agent actions
- User control over memory retention and sharing
- Audit trails for transparency and compliance



## Technical Architecture

### Agent Layer
```
Agent Configuration → Agent Orchestration → Plugin Execution → Workflow Engine
       ↓                      ↓                    ↓              ↓
   Personality &         Prompt Composition    External APIs    Multi-Step
   Capabilities          & Context Building    & Services       Processes
```

### Plugin Ecosystem
```
BasePlugin Interface
    ├── GmailPlugin (email reading, sending, searching)
    ├── CalendarPlugin (event management, scheduling)
    ├── WebSearchPlugin (web search with citations)
    ├── RAGPlugin (knowledge base queries)
    └── [Future Plugins...]
```

### Plugin Architecture (Python)
Plugins are Python-only modules loaded via manifest entrypoints and called directly. They expose a read-only Execute API and optional Actions API for write operations (with preview/approval). Host capabilities (cache, identity, secrets, http, etc.) are optional helpers; plugins may implement equivalents internally. Some provider capabilities require official SDKs (e.g., authenticated HTTP flows, signed downloads/uploads), and plugins may be required to use provider SDKs for those features. Per-action RBAC is enforced by the host. See docs/contracts/PLUGIN_CONTRACT.md for the canonical contract.


### Proactive Ingestion and Delta Sync
The platform silently ingests sources in the background: bulk on first connect, then watermark-based deltas. Stored artifacts feed the indexer and agent pipelines (classification, profiling, enrichment). Briefings and decisions read from indexed data by default; live reads are used sparingly for freshness. Actions apply via plugin plugins with RBAC and approvals.


### Memory Architecture
```
User Memory ← Conversation Memory → Organizational Intelligence
     ↓              ↓                        ↓
Preferences &   Context Building      Cross-User Patterns
Patterns        & Insights            (Privacy-Aware)
```

### Workflow System
```
Workflow Template → Workflow Execution → Step Processing
       ↓                    ↓                  ↓
   Step Definitions    State Management    Plugin Calls &
   & Conditions        & Persistence       LLM Analysis
```



## Architecture Evolution

The Shu platform evolves from a traditional RAG-centric system to an extensible AI operating system for work through careful architectural transformation:

1. **Foundation Preservation**: All existing RAG and chat capabilities remain intact
2. **Agent Layer Addition**: New agent system built on top of existing model configurations
3. **Plugin Standardization**: Existing integrations converted to unified plugin interfaces
4. **Workflow Integration**: Multi-step processes layered over existing services
5. **Memory Enhancement**: User context and intelligence built from existing data

This approach ensures continuous functionality while enabling the transformation to an agentic platform that can support "chief of staff"-style experiences alongside other workflows.
