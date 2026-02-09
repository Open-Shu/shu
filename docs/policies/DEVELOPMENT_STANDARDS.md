# Development Standards

This document establishes development standards for the Shu project. These standards ensure consistency, maintainability, and quality across all development phases.

## Task Authoring Standards (Mandatory, for task docs only)
- These requirements apply to implementation/task documentation under `docs/tasks/EPIC-*/`, not to high-level docs like README, ARCHITECTURE, or TESTING.
- Every task file must include:
  - Implementation Status
  - Limitations/Known Issues
  - Security Vulnerabilities (when applicable to security behavior)
  - Why this task is useful (user-facing value)
  - Scope
  - Acceptance Criteria
- If the user-facing value is not obvious, pause and ask; update the task before implementing.
- Avoid marketing language; reflect actual implementation state only.


## **1. Directory Structure & Organization**
- **Backend application** → `backend/src/shu/` (src-layout)
- **Backend Dockerfile & tooling** → `deployment/docker/api/Dockerfile`, `backend/scripts/`, `backend/alembic/`
- **Frontend application** → `frontend/`
- **Tests** → `tests/` at repo root
- **Configuration files** → repo root (`requirements.txt`, `.env`, etc.)
- **Documentation** → `docs/`
- **Deployment assets** → `deployment/compose/docker-compose.yml`

## **2. Import Standards**
- Use **absolute imports** from the package root: `shu.*` (not `src.*`).
- Add `--app-dir backend/src` when launching uvicorn from repo root, or run from within `backend/`.
- Group imports: standard library → third-party → local imports
- Example: `from shu.core.database import get_async_session_local`

## **3. Configuration Management**

### **3.1. Configuration Architecture**
Shu uses a **hierarchical configuration system** with proper priority cascade:

```
Configuration Priority Cascade:
User Preferences → Model Config → Knowledge Base Config → Global Defaults
```

### **3.2. ConfigurationManager (REQUIRED)**
- **ALL configuration values** must go through `ConfigurationManager`
- **NO hardcoded configuration values** in business logic
- **ALWAYS use dependency injection** for better testability and loose coupling
- **NEVER create ConfigurationManager instances directly**

```python
# CORRECT: Dependency injection in API endpoints
from fastapi import Depends
from ..core.config import get_config_manager_dependency, ConfigurationManager

async def some_endpoint(
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    rag_config = await config_manager.get_rag_configuration(
        knowledge_base_id=kb_id,
        user_id=user_id
    )
    threshold = rag_config.get("search_threshold")

# CORRECT: Dependency injection in services
class SomeService:
    def __init__(self, db: AsyncSession, config_manager: ConfigurationManager):
        self.db = db
        self.config_manager = config_manager

# Usage in endpoint:
async def endpoint(
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
    db: AsyncSession = Depends(get_db)
):
    service = SomeService(db, config_manager)

# LEGACY: Global instance (backward compatibility only)
from ..core.config import get_config_manager
config_manager = get_config_manager()  # Only for existing code

# WRONG: Direct instantiation
config_manager = ConfigurationManager(settings)  # NEVER DO THIS

# WRONG: Hardcoded values
threshold = 0.7  # NEVER DO THIS
```

### **3.3. Configuration Boundaries**
- **Users can configure**: Memory settings, UI/UX preferences (theme, language, timezone)
- **Users CANNOT configure**: RAG thresholds, LLM temperature, system settings
- **Admins configure**: RAG settings via Knowledge Base config, LLM settings via Model config
- **System provides**: Global defaults for all unconfigured values

### **3.4. Migration from Singleton Pattern (REQUIRED)**

**When refactoring existing code that uses the singleton pattern:**

```python
# OLD: Remove singleton usage (refactor away)
from ..core.config import get_config_manager
config_manager = get_config_manager()

# NEW: Use dependency injection
# In API endpoints:
async def endpoint(
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    pass

# In service classes:
class Service:
    def __init__(self, db: AsyncSession, config_manager: ConfigurationManager):
        self.config_manager = config_manager

# In service instantiation:
service = Service(db, config_manager)  # Pass config_manager explicitly
```

**Migration Checklist:**
1. Remove `get_config_manager()` calls from service constructors
2. Add `config_manager` parameter to service constructors
3. Update service instantiation to pass `config_manager`
4. Use dependency injection in API endpoints
5. Test that configuration cascade still works correctly

### **3.5. Environment Variables**
- **System-level configuration only** (database URLs, API keys, global limits)
- Use `src/shu/core/config.py` with Pydantic Settings pattern
- **No hardcoded credentials** or sensitive data in code
- Support empty/optional values where appropriate

### **3.6. Frontend Configuration**
- **Fetch configuration from backend APIs** - never hardcode defaults
- Use backend-provided defaults via ConfigurationManager
- Only hardcode UI constants (colors, layout values, etc.)

```javascript
// CORRECT: Fetch from backend
const { data: kbConfig } = useQuery(['kb-config', kbId],
  () => knowledgeBaseAPI.getRAGConfig(kbId)
);

// WRONG: Hardcoded configuration
const [threshold, setThreshold] = useState(0.7); // NEVER DO THIS
```

## **4. Database Standards**
- **Centralized connection handling** in each module
- Convert empty strings to `None` for optional PostgreSQL credentials:
  ```python
  user = os.getenv("PG_USER") or None
  password = os.getenv("PG_PASSWORD") or None
  ```
- Use **context managers** or explicit connection cleanup
- **No schema dropping** in tests - use targeted cleanup instead

### 4.1. SQLAlchemy NULL Checks (CRITICAL)
**NEVER use Python's `is None` or `is not None` in SQLAlchemy queries** - it doesn't generate proper SQL.

```python
# WRONG: Python 'is None' doesn't generate SQL IS NULL
select(Model).where(Model.column is None)
select(Model).where(or_(Model.column is None, Model.column <= value))

# CORRECT: Use .is_(None) or == None for proper SQL generation
select(Model).where(Model.column.is_(None))
select(Model).where(Model.column == None)  # Also works
select(Model).where(or_(Model.column.is_(None), Model.column <= value))

# WRONG: Python 'is not None' doesn't generate SQL IS NOT NULL
select(Model).where(Model.column is not None)

# CORRECT: Use .is_not(None) or != None
select(Model).where(Model.column.is_not(None))
select(Model).where(Model.column != None)  # Also works
```

**Why this matters**: Python's `is` operator checks object identity, not value equality. SQLAlchemy needs to intercept the comparison to generate SQL, which only works with `.is_()`, `.is_not()`, `==`, or `!=`.

**Prevention**: This pattern is difficult to catch with linters. Code reviews must check for this pattern in all SQLAlchemy queries.

### 4.2. Time & Timezones (REQUIRED)

- Always use timezone-aware datetimes in backend code (tzinfo set) and normalize to UTC.
- When generating timestamps, use `datetime.now(timezone.utc)`.
- When parsing external timestamps, preserve timezone if present; if a naive datetime is encountered, explicitly set it to UTC immediately.
- Never compare aware vs. naive datetimes; normalize before comparison or arithmetic.
- Database storage should use PostgreSQL `timestamptz` (UTC). Convert to user-local timezones only at presentation/UI boundaries.
- Example:
  ```python
  from datetime import datetime, timezone
  now_utc = datetime.now(timezone.utc)
  # normalize possibly-naive input
  dt = parsed or None
  if dt is not None and dt.tzinfo is None:
      dt = dt.replace(tzinfo=timezone.utc)
  ```

## **5. Testing Standards**
- **Functional test coverage** for each phase/component
- Use **mocking** for external services (Google Drive, etc.)
- **Isolated test data** with unique identifiers and cleanup
- Test files follow `test_[component]_[type].py` naming
- Include both unit tests and end-to-end tests
- Test runner scripts use `run_` prefix

## **6. Error Handling & Logging**
- Use Python's `logging` module consistently
- **Descriptive error messages** with context
- Handle external service failures gracefully
- Log at appropriate levels (INFO for progress, ERROR for failures)

## **7. Code Organization**
- **Single responsibility** for modules and classes
- Use **dataclasses** for data containers
- **Type hints** for function parameters and returns
- Keep functions focused and reusable

### 7.1 SOLID & DRY principles and Separation of Concerns Focus (Mandatory)
- Follow SOLID principles - single responsibility and separation of concerns, open/closed, LSP, dependency inversion.
- Follow DRY principles - don't duplicate code or data - consolidate and re-use.
- Keep layers explicit and small:
  - Routers: HTTP wiring only (params, RBAC deps, envelope responses). No business logic. Simple endpoint-specific request/response models (e.g., `CodeRequest(code: str)`) may be defined inline.
  - Schemas: Shared Pydantic models under `src/shu/schemas/*`. Use for: models reused across multiple endpoints, domain schemas with complex validation, and infrastructure schemas (e.g., `SuccessResponse`). Move models to schemas when they grow beyond simple request wrappers.
  - Services/Orchestrators: Implement use-cases by coordinating repositories/strategies. No env reads; fetch via `ConfigurationManager`.
  - Repositories: Encapsulate SQLAlchemy queries per entity; always eager-load relationships (selectinload) to avoid greenlet errors.
  - Strategies: Algorithm variants (e.g., search types, file extractors) live in separate modules.
  - Utilities/Constants: Shared helpers and large constants live in dedicated modules.
- Backend rules:
  - Do not keep large constants (e.g., stop-word sets) in services. Import from a `constants.py` module.
  - Services should accept narrow, injected dependencies rather than constructing heavy clients internally.
  - No environment reads or global singletons in business logic; use dependency injection (`get_config_manager_dependency`).
- Frontend rules:
  - Separate containers (data wiring via React Query) from presentational components (rendering + minimal local state).
  - Move data/side-effect logic (fetching, mutations, streaming, optimistic updates) into custom hooks (use*).
  - Split oversized components (> ~500 LOC) into focused subcomponents/dialogs.
  - Split `frontend/src/services/api.js` into domain modules with a shared axios client+interceptors.
- Enforcement checklist:
  - [ ] Routers define zero shared Pydantic models (inline simple request wrappers are allowed)
  - [ ] Files > ~500 LOC reviewed for decomposition
  - [ ] No env reads outside config.py (backend) / shared axios client (frontend)
  - [ ] Services do not perform direct SQL when a repository exists
- Migration policy:
  - Prefer incremental slices with tests green at each step; avoid dual-support of legacy paths (no prod yet)
  - No DB wipes; any schema changes follow Alembic policy

## **8. Documentation Standards**
- **Docstrings** for all public functions and classes
- Update `SHU_TECHNICAL_ROADMAP.md` for architectural and roadmap changes
- Code comments for complex logic, not obvious operations

## **9. Dependency Management**
- **Pin major versions** in `requirements.txt`
- Include comments explaining package purposes
- Keep dependencies minimal and focused
- Example: `numpy>=1.21.0  # for embedding operations and testing`

## **10. Security & Privacy**
- **Base64 encode** Google service account credentials
- Use environment variables for all sensitive data
- **No credentials in logs** or error messages
- Validate all input parameters

## **11. Development Workflow**
- **Read existing files** before making changes to avoid overwrites
- **Follow DRY principle** - don't repeat code or documentation
- Make **incremental, testable changes**
- Focus on engineering solutions, not marketing language

## **12. Phase-Specific Standards**
- **Phase isolation** - each phase should be independently testable
- **Backward compatibility** when possible
- **Clean interfaces** between components

## **13. Naming Conventions**
- **Files**: `snake_case.py`
- **Classes**: `PascalCase`
- **Functions/Variables**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`
- **Test methods**: `test_[functionality]_[scenario]()`

## **14. Git & Version Control**
- Commit messages should be descriptive and reference changes
- Keep commits focused on single logical changes
- Update documentation with code changes

## **15. Frontend Standards (React/JavaScript)**

### **15.1. React Component Standards**
- **Functional components** with hooks (no class components)
- **Component naming**: PascalCase (e.g., `KnowledgeBases.js`)
- **File naming**: PascalCase for components, camelCase for utilities
- **Props destructuring** for cleaner component signatures
- **Custom hooks** for reusable logic (prefixed with `use`)

### **15.2. State Management**
- **React Query** for server state management
- **useState** for local component state
- **useContext** for global state when needed
- **Custom hooks** for complex state logic

### **15.3. API Integration**
- **Axios** for HTTP requests with interceptors
- **React Query** for caching and background updates
- **Error handling** with user-friendly messages
- **Loading states** for all async operations

### **15.4. UI/UX Standards**
- **Material-UI** as the primary component library
- **Responsive design** for mobile and desktop
- **Consistent spacing** using Material-UI's spacing system
- **Accessibility** with proper ARIA labels and keyboard navigation
- **Error boundaries** for graceful error handling

### **15.5. Code Organization (Frontend)**
```
frontend/
├── src/
│   ├── components/          # React components
│   │   ├── Dashboard.js
│   │   ├── KnowledgeBases.js
│   │   └── ...
│   ├── services/           # API and utility services
│   │   └── api.js
│   ├── hooks/              # Custom React hooks
│   ├── utils/              # Utility functions
│   ├── App.js              # Main application
│   └── index.js            # Entry point
├── public/                 # Static assets
└── package.json
```

### **15.6. Frontend Testing Standards**
- **React Testing Library** for component testing
- **Jest** for unit testing
- **User-centric tests** focusing on behavior over implementation
- **Mock API calls** for isolated component testing

### **15.7. Frontend Performance**
- **Code splitting** with React.lazy for route-based splitting
- **Memoization** with React.memo and useMemo for expensive operations
- **Bundle optimization** with proper tree shaking
- **Image optimization** for static assets

### **15.8. Frontend Security**
- **Input validation** on client and server
- **XSS prevention** with proper escaping
- **CORS configuration** for API calls
- **Environment variables** for sensitive configuration


### **15.9. Frontend Logging**
- Use the shared logging utility instead of raw console.* calls: `frontend/src/utils/log.js`
- The utility gates debug/info/warn logs with `REACT_APP_DEBUG==='true'` or non-production `NODE_ENV`
- Always use:
  - `log.debug()` for verbose development logs
  - `log.info()` for notable but non-error events
  - `log.warn()` for recoverable issues
  - `log.error()` for actual errors (always logs)
- New code should not introduce raw `console.*` calls; refactor existing logs opportunistically

## **16. API Integration Standards**

### **16.1. Backend-Frontend Communication**
- **RESTful API** with consistent endpoint patterns
- **JSON envelope format** for all responses (see `docs/policies/API_RESPONSE_STANDARD.md`)
- **HTTP status codes** for proper error handling
- **Request/response validation** with Pydantic schemas

### **16.2. Error Handling**
- **Consistent error format** across all endpoints
- **User-friendly error messages** in frontend
- **Proper HTTP status codes** for different error types
- **Error logging** for debugging and monitoring

### **16.3. Data Flow**
- **React Query** for server state synchronization
- **Optimistic updates** for better UX
- **Background refetching** for real-time data
- **Cache invalidation** strategies

## **17. Development Environment Standards**

### **17.1. Local Development**
- **Docker Compose** for full-stack development
- **Hot reloading** for both frontend and backend
- **Shared environment variables** between services
- **Database migrations** with Alembic (see `docs/policies/DB_MIGRATION_POLICY.md` for development vs. release squashing policy)

### **17.2. Development Tools**
- **ESLint** for JavaScript/React code quality
- **Prettier** for code formatting
- **TypeScript** for type safety (future consideration)
- **Storybook** for component development (future consideration)

## **18. Deployment Standards**

### **18.1. Containerization**
- **Multi-stage Docker builds** for optimization
- **Non-root users** for security
- **Health checks** for container orchestration
- **Environment-specific configurations**

### **18.2. Production Considerations**
- **Environment variables** for all configuration
- **Secrets management** for sensitive data
- **Monitoring and logging** for observability
- **Backup strategies** for data persistence

## **19. Authentication & Authorization Standards**

### **19.1. Dual Authentication System**
Shu supports two authentication methods:

1. **Google OAuth2**: For users with Google accounts
2. **Password Authentication**: For users without Google accounts (e.g., investors)

### **19.2. Google SSO Integration**
- **OAuth2 Flow**: Use Google OAuth2 for Google account authentication
- **JWT Tokens**: Issue JWT tokens for session management
- **Auto-Provisioning**: Automatically create users on first login
- **Role Assignment**: Assign roles based on email domain or group membership

```python
# Google Authentication Pattern
class GoogleSSOAuth:
    async def authenticate_user(self, google_token: str) -> User:
        google_user = await self.google_client.verify_token(google_token)
        user = await self.user_service.get_or_create_user(google_user)
        return user
```

### **19.3. Password Authentication & Security Model**
- **Secure Hashing**: Use bcrypt for password hashing
- **Database Storage**: Store users in PostgreSQL database
- **Secure Registration**: Self-registered users are inactive by default
- **Admin Activation**: Requires administrator activation for security
- **Role Enforcement**: Self-registered users are forced to "regular_user" role
- **Admin Creation**: Allow admins to create active investor accounts

```python
# Secure Password Authentication Pattern
class PasswordAuthService:
    async def create_user(self, email: str, password: str, name: str, role: str, db: AsyncSession, admin_created: bool = False) -> User:
        # Security: Only admins can create users with custom roles or active status
        if not admin_created:
            role = "regular_user"  # Force regular_user role for self-registration
            is_active = False      # Require admin activation
        else:
            is_active = True  # Admin-created users are active by default

        password_hash = self._hash_password(password)
        user = User(email=email, name=name, password_hash=password_hash, auth_method="password", role=role, is_active=is_active)
        db.add(user)
        await db.commit()
        return user

    async def authenticate_user(self, email: str, password: str, db: AsyncSession) -> User:
        user = await self._get_user_by_email(email, db)
        if not user.is_active:
            raise ValueError("User account is inactive. Please contact an administrator for activation.")
        if not self._verify_password(password, user.password_hash):
            raise ValueError("Invalid credentials")
        return user
```

### **19.2. RBAC Implementation**
- **Role-Based Access**: Admin, Power User, Regular User, Read Only
- **Knowledge Base Permissions**: Owner, Admin, Member, Read Only
- **Resource-Level Control**: Fine-grained permissions per knowledge base
- **Audit Logging**: Log all permission checks and access attempts

```python
# Standard RBAC Pattern
class RBACController:
    async def can_access_kb(self, user_id: str, kb_id: str) -> bool:
        user = await self.get_user(user_id)
        kb = await self.get_knowledge_base(kb_id)
        return await self.check_permissions(user, kb)
```

## **20. LLM Integration Standards**

### **20.1. Multi-Provider Support**
- **Provider Abstraction**: Support OpenAI, Anthropic, Ollama through unified interface
- **Configuration Management**: Environment-based provider configuration
- **Fallback Strategy**: Graceful fallback between providers
- **Rate Limiting**: Implement rate limiting per provider

```python
# Standard LLM Client Pattern
class LLMClient:
    def __init__(self, provider: str, api_key: str, base_url: str = None):
        self.provider = provider
        self.client = self._create_client(provider, api_key, base_url)

    async def generate_response(self, messages: List[Dict], model: str):
        response = await self.client.chat.completions.create(
            model=model, messages=messages
        )
        return response.choices[0].message.content
```

### **20.2. Prompt Management**
- **Template System**: Structured prompt templates with variables
- **Version Control**: Track prompt template versions
- **A/B Testing**: Support for prompt experimentation
- **Context Management**: Maintain conversation context efficiently

## **21. Agentic AI Development Standards**

### **21.1. Agent Architecture Standards**
- **Base Agent Pattern**: All agents must inherit from `BaseAgent` abstract class
- **Agent Registration**: Agents register capabilities and event subscriptions
- **Event-Driven Design**: Agents respond to events, not direct calls
- **Stateless Operations**: Agents should be stateless for scalability
- **Health Monitoring**: All agents must implement health check endpoints

```python
# Standard Agent Implementation Pattern
class PersonalProfileAgent(BaseAgent):
    def __init__(self, user_id: str):
        super().__init__(
            agent_id=f"profile-{user_id}",
            capabilities=["user_profiling", "behavior_analysis"]
        )
        self.user_id = user_id

    async def handle_event(self, event: UserEvent) -> AgentResponse:
        """Handle user events and return appropriate response"""
        pass

    async def get_health_status(self) -> HealthStatus:
        """Return current agent health status"""
        pass
```

### **21.2. Privacy-First Development**
- **Data Isolation**: User data must be isolated by default
- **Access Control**: All data access goes through privacy controller
- **Audit Logging**: Detailed logging of all data access
- **Anonymization**: Cross-user insights must be anonymized
- **Right to be Forgotten**: Support for complete data deletion

```python
# Privacy Controller Pattern
class PrivacyController:
    async def filter_results(self, results: List[Document], user_id: str) -> List[Document]:
        """Apply privacy filtering to all query results"""
        filtered = []
        for doc in results:
            if await self.can_access_document(user_id, doc):
                filtered_doc = await self.apply_content_redaction(doc, user_id)
                filtered.append(filtered_doc)
        return filtered
```

### **21.3. Multi-Tier Knowledge Base Standards**
- **Hierarchical Structure**: Support user/team/company knowledge bases
- **Access Inheritance**: Team members inherit team KB access
- **Content Deduplication**: Shared content stored once with references
- **Cross-KB Queries**: Support querying multiple KBs with permission filtering

### **21.4. Event System Standards**
- **Event Schema**: All events must follow standardized schema
- **Async Processing**: Events processed asynchronously
- **Error Handling**: Failed event processing must not block other events
- **Event Replay**: Support for event replay for debugging

```python
# Standard Event Schema
class UserEvent(BaseModel):
    event_id: str
    user_id: str
    event_type: str
    timestamp: datetime
    data: Dict[str, Any]
    privacy_level: str
```

## **22. Deployment Architecture Standards**

### **22.1. Multi-Mode Application**
- **Mode-Based Startup**: Application supports API, Agents, Worker, and All modes
- **Component Isolation**: Different modes load only required components
- **Resource Optimization**: Each mode optimized for its specific workload
- **Development Flexibility**: All modes can run together in development

```python
# Standard Application Mode Pattern
class ShuApplication:
    def __init__(self, mode: str):
        self.mode = mode
        self.setup_components()

    def setup_components(self):
        if self.mode == "api":
            self.setup_api_server()
        elif self.mode == "agents":
            self.setup_agent_framework()
        elif self.mode == "worker":
            self.setup_background_jobs()
```

### **22.2. Container Standards**
- **Single Image**: One Docker image supports all deployment modes
- **Environment Configuration**: Mode determined by environment variables
- **Resource Limits**: Appropriate resource limits per deployment mode
- **Health Checks**: Each mode implements appropriate health checks

### **22.3. Kubernetes Deployment**
- **Separate Deployments**: API, Agents, and Workers deploy separately
- **Independent Scaling**: Each component scales based on its workload
- **Service Discovery**: Components communicate through Kubernetes services
- **Configuration Management**: ConfigMaps and Secrets for environment-specific config

## **23. Performance & Scalability Standards**

### **23.1. Query Performance**
- **Response Time**: <500ms for multi-KB queries
- **Caching Strategy**: Implement Redis caching for frequent queries
- **Connection Pooling**: Use connection pools for database access
- **Query Optimization**: Monitor and optimize slow queries

### **23.2. Agent Performance**
- **Event Processing**: <2 seconds for agent event processing
- **Memory Management**: Agents must manage memory efficiently
- **Concurrent Processing**: Support concurrent event processing
- **Resource Limits**: Implement resource limits per agent

### **23.3. Scalability Patterns**
- **Horizontal Scaling**: Design for multiple agent instances
- **Load Balancing**: Support load balancing across agent instances
- **State Management**: Use external state storage (Redis/Database)
- **Circuit Breakers**: Implement circuit breakers for external services

## **24. Monitoring & Observability Standards**

### **24.1. Logging Standards**
- **Structured Logging**: Use structured JSON logging
- **Correlation IDs**: Include correlation IDs for request tracing
- **Privacy-Safe Logging**: Never log sensitive user data
- **Log Levels**: Use appropriate log levels (DEBUG, INFO, WARN, ERROR, CRITICAL)

```python
# Standard Logging Pattern
import structlog

logger = structlog.get_logger(__name__)

async def process_user_event(event: UserEvent):
    logger.info(
        "Processing user event",
        event_id=event.event_id,
        user_id=event.user_id,
        event_type=event.event_type
    )
```

### **24.2. Metrics & Monitoring**
- **Agent Health Metrics**: Monitor agent uptime and performance
- **Query Performance Metrics**: Track query response times
- **User Activity Metrics**: Monitor user engagement (privacy-safe)
- **System Resource Metrics**: Monitor CPU, memory, database performance

### **24.3. Alerting Standards**
- **Critical Alerts**: Agent failures, database connectivity issues
- **Performance Alerts**: Query response time degradation
- **Privacy Alerts**: Unauthorized data access attempts
- **Business Alerts**: Significant drops in user engagement

## **25. Cache Usage Standards**

### **25.1. CacheBackend Interface (REQUIRED)**
- **ALL caching operations** must use the unified `CacheBackend` interface
- **NO direct Redis client usage** for caching purposes
- **NO custom cache implementations** - use provided backends only
- **ALWAYS use dependency injection** for CacheBackend access

```python
# CORRECT: Use CacheBackend interface
from fastapi import Depends
from shu.core.cache_backend import CacheBackend, get_cache_backend_dependency

async def some_endpoint(
    cache: CacheBackend = Depends(get_cache_backend_dependency)
):
    value = await cache.get("my_key")
    await cache.set("my_key", "my_value", ttl_seconds=300)

# CORRECT: Service with CacheBackend dependency
class SomeService:
    def __init__(self, cache: CacheBackend):
        self.cache = cache

# WRONG: Direct Redis usage
from shu.core.database import get_redis_client
redis = get_redis_client()  # NEVER DO THIS FOR CACHING

# WRONG: Custom cache implementation
class MyCustomCache:  # NEVER DO THIS
    pass
```

### **25.2. Backend Selection**
- **Automatic selection** based on `SHU_REDIS_URL` configuration
- **Redis backend**: Used when `SHU_REDIS_URL` is set and Redis is reachable
- **In-memory backend**: Used for development or when Redis is unavailable
- **Transparent fallback**: Application works identically with both backends

### **25.3. Key Namespacing**
- **ALWAYS namespace cache keys** to prevent collisions
- **Use consistent namespace patterns** for different consumers

```python
# Standard namespace patterns:
# Plugin cache: tool_cache:{plugin_name}:{user_id}:{key}
# Rate limiting: rl:{type}:{identifier}
# Configuration: config:{service}:{key}
# General cache: cache:{service}:{key}

# CORRECT: Namespaced keys
await cache.set(f"tool_cache:{plugin_name}:{user_id}:last_sync", timestamp)
await cache.set(f"rl:api:user:{user_id}", "10")
await cache.set(f"config:rag:{kb_id}", json.dumps(config))

# WRONG: Non-namespaced keys
await cache.set("last_sync", timestamp)  # NEVER DO THIS
await cache.set("user_limit", "10")      # NEVER DO THIS
```

### **25.4. When to Use @lru_cache vs CacheBackend**

**Use `@lru_cache` for:**
- **Pure functions** with deterministic outputs
- **Expensive computations** that don't change during process lifetime
- **Configuration parsing** or constant data
- **Single-process caching** where data loss on restart is acceptable

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def parse_complex_config(config_string: str) -> Dict:
    """Parse configuration - result never changes for same input"""
    return expensive_parsing_operation(config_string)

@lru_cache(maxsize=1)
def get_system_constants() -> Dict:
    """Load system constants - same for entire process lifetime"""
    return load_constants_from_file()
```

**Use `CacheBackend` for:**
- **User-specific data** that needs persistence across requests
- **Shared state** between multiple processes or instances
- **TTL-based expiration** requirements
- **Rate limiting** or quota tracking
- **Plugin data** or user preferences
- **Any data that should survive process restarts**

## **26. Queue Usage Standards**

### **26.1. QueueBackend Interface (REQUIRED)**
- **ALL queue operations** must use the unified `QueueBackend` interface
- **NO direct Redis client usage** for queue purposes
- **NO custom queue implementations** - use provided backends only
- **ALWAYS use dependency injection** for QueueBackend access

```python
# CORRECT: Use QueueBackend interface
from fastapi import Depends
from shu.core.queue_backend import QueueBackend, get_queue_backend_dependency
from shu.core.workload_routing import WorkloadType, enqueue_job

async def some_endpoint(
    queue: QueueBackend = Depends(get_queue_backend_dependency)
):
    # Enqueue a job using WorkloadType routing
    job = await enqueue_job(
        backend=queue,
        workload_type=WorkloadType.INGESTION,
        payload={"document_id": "doc-123"}
    )

# CORRECT: Service with QueueBackend dependency
class SomeService:
    def __init__(self, queue: QueueBackend):
        self.queue = queue

# WRONG: Direct Redis usage for queues
from shu.core.database import get_redis_client
redis = get_redis_client()
await redis.lpush("my_queue", job_data)  # NEVER DO THIS FOR QUEUES

# WRONG: Custom queue implementation
class MyCustomQueue:  # NEVER DO THIS
    pass
```

### **26.2. Backend Selection**
- **Automatic selection** based on `SHU_REDIS_URL` configuration
- **Redis backend**: Used when `SHU_REDIS_URL` is set and Redis is reachable
- **In-memory backend**: Used for development or when Redis is unavailable
- **Transparent operation**: Application works identically with both backends

### **26.3. WorkloadType Routing (REQUIRED)**
- **ALWAYS use WorkloadType** when enqueuing jobs - never hardcode queue names
- **Use the `enqueue_job` helper** from `workload_routing.py`
- **Available WorkloadTypes**: INGESTION, INGESTION_OCR, INGESTION_EMBED, LLM_WORKFLOW, MAINTENANCE, PROFILING

```python
from shu.core.workload_routing import WorkloadType, enqueue_job, get_queue_name

# CORRECT: Use WorkloadType routing
await enqueue_job(
    backend=queue,
    workload_type=WorkloadType.PROFILING,
    payload={"document_id": doc_id}
)

# CORRECT: Get queue name for a workload type
queue_name = get_queue_name(WorkloadType.INGESTION)

# WRONG: Hardcoded queue names
await queue.enqueue(Job(queue_name="my_queue", payload=data))  # NEVER DO THIS
```

### **26.4. Job Processing Pattern**
- **Use the Worker class** for consuming jobs from queues
- **Configure workers with WorkloadTypes** they should consume
- **Implement proper acknowledgment** after successful processing
- **Use reject with requeue** for transient failures

```python
from shu.core.worker import Worker, WorkerConfig
from shu.core.workload_routing import WorkloadType

# Configure worker for specific workload types
config = WorkerConfig(
    workload_types={WorkloadType.INGESTION, WorkloadType.PROFILING},
    poll_interval=1.0,
    shutdown_timeout=30.0
)

# Worker handles dequeue → process → acknowledge flow
worker = Worker(backend=queue, config=config, job_handler=process_job)
await worker.run()
```

### **26.5. Migration from asyncio.create_task**
- **Replace fire-and-forget tasks** with queue-based processing
- **Benefits**: Visibility, reliability, horizontal scaling, retry support

```python
# OLD: Fire-and-forget (no visibility, lost on crash)
asyncio.create_task(process_document(doc_id))

# NEW: Queue-based (visible, reliable, scalable)
await enqueue_job(
    backend=queue,
    workload_type=WorkloadType.PROFILING,
    payload={"document_id": doc_id}
)
```

## **Quick Reference for LLMs**

When developing, prioritize:
1. **Structure**: Code in `src/`, frontend in `frontend/`, tests in root
2. **Imports**: Use `from src.module import` for backend, relative imports for frontend
3. **Config**: Environment variables only
4. **Database**: Handle empty PG_USER/PG_PASSWORD
5. **Testing**: Mock externals, isolated cleanup
6. **Security**: No hardcoded secrets
7. **Standards**: Type hints, docstrings, logging
8. **Frontend**: React functional components, Material-UI, React Query
9. **API**: RESTful endpoints with envelope format, proper error handling
10. **Deployment**: Docker containerization, environment-based configuration
11. **Authentication**: Google SSO, RBAC, JWT tokens
12. **LLM Integration**: Multi-provider support, rate limiting, prompt templates
13. **Agents**: BaseAgent inheritance, event-driven, privacy-first
14. **Deployment**: Multi-mode application, container standards, Kubernetes
15. **Performance**: <500ms queries, <2s agent processing, caching
16. **Privacy**: Data isolation, access control, audit logging
17. **Monitoring**: Structured logging, health metrics, alerting
18. **Caching**: Use CacheBackend interface, namespace keys, graceful error handling
19. **Queues**: Use QueueBackend interface, WorkloadType routing, no direct Redis for queues
