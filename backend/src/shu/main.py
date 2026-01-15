"""Shu - FastAPI Application

This module creates and configures the FastAPI application for Shu.
"""

import asyncio
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .api.auth import router as auth_router
from .api.branding import router as branding_router
from .api.chat import router as chat_router
from .api.chat_plugins import router as chat_plugins_router
from .api.config import router as config_router
from .api.experiences import router as experiences_router
from .api.groups import router as groups_router
from .api.health import router as health_router
from .api.host_auth import public_router as host_auth_public_router
from .api.host_auth import router as host_auth_router
from .api.knowledge_bases import router as knowledge_bases_router
from .api.llm import router as llm_router
from .api.model_configuration import router as model_configuration_router
from .api.permissions import router as permissions_router
from .api.plugins_router import router as plugins_router
from .api.prompts import router as prompts_router
from .api.query import router as query_router
from .api.resources import router as resources_router
from .api.side_call import router as side_call_router
from .api.system import router as system_router
from .api.user_permissions import router as user_permissions_router
from .api.user_preferences import router as user_preferences_router
from .core.config import get_settings_instance
from .core.database import init_db
from .core.exceptions import ShuException
from .core.http_client import close_http_client
from .core.logging import get_logger, setup_logging
from .core.middleware import (
    AuthenticationMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    TimingMiddleware,
)
from .plugins.request_limits import RequestSizeLimitMiddleware

logger = get_logger(__name__)
settings = get_settings_instance()


class StripAPITrailingSlashMiddleware(BaseHTTPMiddleware):
    """Normalize API paths to avoid 307 redirects while preserving headers.

    Rule: For all API paths, remove a trailing slash (except the exact API prefix),
    so both '/foo' and '/foo/' resolve to the same handler without redirects.
    """

    def __init__(self, app, api_prefix: str):
        super().__init__(app)
        self.api_prefix = api_prefix.rstrip("/")

    async def dispatch(self, request, call_next):
        path = request.scope.get("path", "")
        # Only touch API paths
        if not path.startswith(self.api_prefix + "/"):
            return await call_next(request)

        # Remove a trailing slash to avoid Starlette redirects
        if path.endswith("/"):
            normalized = path.rstrip("/")
            # Do not collapse the API prefix itself
            if normalized != self.api_prefix:
                request.scope["path"] = normalized
        return await call_next(request)


def generate_error_id() -> str:
    """Generate a unique error ID for tracking."""
    return f"ERR-{uuid.uuid4().hex[:8].upper()}"


def get_request_context(request: Request) -> dict[str, Any]:
    """Extract relevant context from request for error logging."""
    return {
        "method": request.method,
        "url": str(request.url),
        "path": request.url.path,
        "query_params": dict(request.query_params),
        "headers": {
            "user-agent": request.headers.get("user-agent"),
            "content-type": request.headers.get("content-type"),
            "accept": request.headers.get("accept"),
            "x-forwarded-for": request.headers.get("x-forwarded-for"),
            "x-real-ip": request.headers.get("x-real-ip"),
        },
        "client": {
            "host": request.client.host if request.client else None,
            "port": request.client.port if request.client else None,
        },
        "request_id": getattr(request.state, "request_id", None),
        "user_id": getattr(request.state, "user_id", None),
    }


def log_exception_details(exc: Exception, request: Request, error_id: str, include_traceback: bool = False) -> None:
    """Log detailed exception information."""
    # Get request context
    request_context = get_request_context(request)

    # Prepare exception details
    exception_details = {
        "error_id": error_id,
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "request_context": request_context,
    }

    # Add traceback in development mode or when explicitly requested
    if include_traceback:
        exception_details["traceback"] = traceback.format_exc()
        exception_details["stack_trace"] = traceback.format_stack()

    # Add exception attributes if available
    if hasattr(exc, "__dict__"):
        exception_details["exception_attributes"] = {
            k: str(v) for k, v in exc.__dict__.items() if not k.startswith("_") and k not in ["args"]
        }

    # Log with appropriate level
    if include_traceback:
        logger.error("Unhandled exception with full details", extra=exception_details, exc_info=True)
    else:
        logger.error("Unhandled exception", extra=exception_details)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Setup logging first
    setup_logging()

    # Startup
    logger.info("Starting Shu...")
    logger.info(f"Version: {settings.version}")
    logger.info(f"Environment: {'Development' if settings.debug else 'Production'}")

    # Initialize database connection
    try:
        await init_db()
        logger.info("Database initialized successfully")

        # Log database configuration
        from .core.database import get_database_url

        db_url = get_database_url()
        # Mask password in URL for security
        if "@" in db_url:
            # Extract host and database name from URL
            parts = db_url.split("@")
            if len(parts) == 2:
                host_db = parts[1].split("/")
                if len(host_db) >= 2:
                    host_port = host_db[0]
                    database_name = host_db[1].split("?")[0]  # Remove query params
                    logger.info(f"Using database: {database_name} on {host_port}")
                else:
                    logger.info(f"Using database URL: {db_url.split('@')[1] if '@' in db_url else 'URL format'}")
            else:
                logger.info(f"Using database URL: {db_url}")
        else:
            logger.info(f"Using database URL: {db_url}")

        # Startup readiness-equivalent warning: DB schema baseline
        try:
            if settings.db_release:
                from .core.database import get_async_session_local
                from .services.system_status import check_db_release

                session_maker = get_async_session_local()
                async with session_maker() as session:
                    rc = await check_db_release(session, settings.db_release)
                if rc.get("error"):
                    logger.warning(f"Startup DB release check error: {rc['error']}")
                elif rc.get("mismatch"):
                    logger.warning(
                        "Startup: DB release mismatch; readiness would fail",
                        extra={"expected": rc["expected"], "current": rc["current"]},
                    )
                else:
                    logger.info(
                        "Startup: DB release matches expected baseline",
                        extra={"db_release": settings.db_release},
                    )
        except Exception as e:
            logger.warning(f"Startup DB release check error: {e}")

    except Exception as e:
        # Do not crash the app if DB is unavailable; log and continue. Health/readiness will reflect DB status.
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        # continue without raising to allow the app to start

    # Source types are initialized via database migrations

    # Preload the default embedding model to avoid lazy loading
    try:
        from .services.rag_processing_service import RAGProcessingService

        logger.info("Preloading default embedding model...")
        RAGProcessingService.get_instance(settings.default_embedding_model)
        logger.info("Default embedding model preloaded successfully")
    except Exception as e:
        logger.error(f"Failed to preload embedding model: {e}", exc_info=True)
        # Don't raise here, as the app can still function without preloaded model

    # Start attachments TTL cleanup scheduler
    try:
        from .services.attachment_cleanup import start_attachment_cleanup_scheduler

        app.state.attachments_cleanup_task = await start_attachment_cleanup_scheduler()
        logger.info("Attachment cleanup scheduler started")
    except Exception as e:
        logger.warning(f"Failed to start attachment cleanup scheduler: {e}")

    # Start Plugin Feeds scheduler (in-process)
    try:
        if getattr(settings, "plugins_scheduler_enabled", True):
            from .services.plugins_scheduler_service import start_plugins_scheduler

            app.state.plugins_scheduler_task = await start_plugins_scheduler()
            logger.info("Plugin Feeds scheduler started")
        else:
            logger.info("Plugin Feeds scheduler disabled by configuration")
    except Exception as e:
        logger.warning(f"Failed to start Plugin Feeds scheduler: {e}")

    # Start Experiences scheduler (in-process)
    try:
        if getattr(settings, "experiences_scheduler_enabled", True):
            from .services.experiences_scheduler_service import start_experiences_scheduler

            app.state.experiences_scheduler_task = await start_experiences_scheduler()
            logger.info("Experiences scheduler started")
        else:
            logger.info("Experiences scheduler disabled by configuration")
    except Exception as e:
        logger.warning(f"Failed to start Experiences scheduler: {e}")

    # Start inline workers if workers are enabled
    try:
        if settings.workers_enabled:
            from .core.worker import Worker, WorkerConfig
            from .core.workload_routing import WorkloadType
            from .worker import process_job

            # Get queue backend (shared by all workers)
            backend = await get_queue_backend()


            # Configure worker to consume all workload types
            config = WorkerConfig(
                workload_types=set(WorkloadType),
                poll_interval=settings.worker_poll_interval,
                shutdown_timeout=settings.worker_shutdown_timeout,
            )


            # Create N concurrent workers
            concurrency = max(1, settings.worker_concurrency)
            app.state.inline_worker_tasks = []

            for i in range(concurrency):
                worker_id = f"{i + 1}/{concurrency}"
                worker = Worker(backend, config, job_handler=process_job, worker_id=worker_id)

                async def run_inline_worker(w=worker, wid=worker_id):
                    try:
                        await w.run()
                    except Exception as e:
                        logger.error(f"Inline worker {wid} error: {e}", exc_info=True)

                task = asyncio.create_task(run_inline_worker())
                app.state.inline_worker_tasks.append(task)

            logger.info(
                f"Inline workers started (concurrency={concurrency}, consuming all workload types)"
            )
        else:
            logger.info("Workers disabled (SHU_WORKERS_ENABLED=false), skipping inline worker startup")
    except Exception as e:
        logger.warning(f"Failed to start inline workers: {e}")

    # Plugins v1: optional auto-sync from plugins to DB registry
    try:
        if getattr(settings, "plugins_auto_sync", False):
            from .core.database import get_async_session_local
            from .plugins.registry import REGISTRY

            session_maker = get_async_session_local()
            async with session_maker() as session:
                stats = await REGISTRY.sync(session)
            logger.info("Plugins auto-sync completed", extra={"stats": stats})
    except Exception as e:
        logger.warning(f"Plugins auto-sync failed: {e}")

    logger.info("Shu startup complete")

    yield

    # Cancel and await background schedulers for clean shutdown
    task_attrs = [
        ('attachments_cleanup', 'attachments_cleanup_task'),
        ('plugins_scheduler', 'plugins_scheduler_task'),
        ('experiences_scheduler', 'experiences_scheduler_task'),
    ]
    tasks_to_cancel = []
    for name, attr in task_attrs:
        task = getattr(app.state, attr, None)
        if task and not task.done():
            tasks_to_cancel.append((name, task))

    # Add inline worker tasks (list of concurrent workers)
    inline_worker_tasks = getattr(app.state, 'inline_worker_tasks', [])
    for i, task in enumerate(inline_worker_tasks):
        if task and not task.done():
            tasks_to_cancel.append((f'inline_worker_{i + 1}', task))

    # Cancel all tasks
    for name, task in tasks_to_cancel:
        task.cancel()


    # Wait for all tasks to complete cancellation
    if tasks_to_cancel:
        for name, task in tasks_to_cancel:
            try:
                await task
            except asyncio.CancelledError:
                logger.debug(f"Background task '{name}' cancelled successfully")
            except Exception as e:
                logger.warning(f"Error while cancelling background task '{name}': {e}")

    # Shutdown
    logger.info("Shutting down Shu...")

    # Clean up all OCR processes (PaddleOCR subprocesses)
    try:
        from .processors.text_extractor import TextExtractor

        TextExtractor.cleanup_ocr_processes()  # Clean up all OCR processes
        logger.info("OCR processes cleaned up")
    except Exception as e:
        logger.warning(f"Error cleaning up OCR processes during shutdown: {e}")

    # Clean up RAG service instances and thread pools
    try:
        from .services.rag_processing_service import clear_rag_service_cache

        clear_rag_service_cache()
        logger.info("RAG service cache cleared")
    except Exception as e:
        logger.warning(f"Error clearing RAG service cache during shutdown: {e}")

    # Close HTTP client connections
    try:
        await close_http_client()
        logger.info("HTTP client connections closed")
    except Exception as e:
        logger.error(f"Error closing HTTP client connections: {e}")

    # Close database connections
    try:
        # Database connections are handled by SQLAlchemy engine
        # and will be closed automatically
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")

    logger.info("Shu shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        description="Shu RAG API",
        version=settings.version,
        debug=settings.debug,
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,  # Disabled; we serve a self-hosted version below
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
        redirect_slashes=False,  # Disable automatic redirects; we normalize via middleware and dual-route registration
    )

    # Mount static files for self-hosted assets (e.g., ReDoc JS)
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Custom ReDoc route using self-hosted JS (avoids CDN tracking prevention issues)
    if settings.debug:

        @app.get("/redoc", include_in_schema=False)
        async def redoc_html() -> HTMLResponse:
            return HTMLResponse(
                f"""<!DOCTYPE html>
<html>
<head>
    <title>{settings.app_name} - ReDoc</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
    <style>body {{ margin: 0; padding: 0; }}</style>
</head>
<body>
    <noscript>ReDoc requires Javascript to function. Please enable it to browse the documentation.</noscript>
    <redoc spec-url="/openapi.json"></redoc>
    <script src="/static/redoc.standalone.js"></script>
</body>
</html>"""
            )

    # Add middleware
    setup_middleware(app)

    # Add exception handlers
    setup_exception_handlers(app)

    # Add routes
    setup_routes(app)

    logger.info("Shu FastAPI application created successfully")
    return app


def setup_middleware(app: FastAPI) -> None:
    """Register and configure middleware for the FastAPI application.

    Sets up request ID, timing, authentication, security headers, API trailing-slash normalization, CORS, a scoped request-size limit for plugin endpoints, trusted-host validation (enforced only in non-debug when `SHU_ALLOWED_HOSTS` is set), and conditionally enables rate limiting based on settings. Middleware ordering is preserved; rate limiting is added after authentication so it can apply user-aware limits.
    """
    settings = get_settings_instance()

    # Request ID middleware (custom)
    app.add_middleware(RequestIDMiddleware)

    # Timing middleware (custom)
    app.add_middleware(TimingMiddleware)

    # Authentication middleware (custom)
    app.add_middleware(AuthenticationMiddleware)

    # Security headers (includes Permissions-Policy for FedCM)
    app.add_middleware(SecurityHeadersMiddleware)

    # Strip trailing slashes for API paths to avoid 307 redirects dropping headers
    app.add_middleware(StripAPITrailingSlashMiddleware, api_prefix=settings.api_v1_prefix)

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=settings.cors_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Plugins request size guard (scoped)
    try:
        app.add_middleware(
            RequestSizeLimitMiddleware,
            max_bytes=1_000_000,
            path_prefix=f"{settings.api_v1_prefix}/plugins",
        )
    except Exception:
        logger.warning("Failed to add RequestSizeLimitMiddleware for plugins")

    # Trusted host middleware
    if not settings.debug and getattr(settings, "allowed_hosts", None) and settings.allowed_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    elif not settings.debug:
        logger.warning(
            "TrustedHostMiddleware not enforcing host validation; set SHU_ALLOWED_HOSTS for non-dev deployments"
        )

    # Rate limiting middleware (applied after authentication to have user context)
    if settings.enable_api_rate_limiting:
        try:
            app.add_middleware(RateLimitMiddleware)
            logger.info("Rate limiting middleware enabled")
        except Exception as e:
            logger.warning("Failed to add RateLimitMiddleware: %s", e)


def setup_exception_handlers(app: FastAPI) -> None:
    """Register custom exception handlers on the FastAPI application.

    Adds handlers for ShuException, HTTPException, and Exception that log errors with contextual request information,
    generate error IDs for server-side errors, and return structured JSON error responses. In development or debug modes
    handlers include additional diagnostic details such as traceback and request info.

    Parameters
    ----------
        app (FastAPI): The FastAPI application instance to configure.

    """
    settings = get_settings_instance()

    @app.exception_handler(ShuException)
    async def shu_exception_handler(request: Request, exc: ShuException):
        """Handle custom Shu exceptions with enhanced logging."""
        # Generate error ID for tracking (for 5xx errors)
        error_id = generate_error_id() if exc.status_code >= 500 else None

        # Log server errors (5xx) with context
        if exc.status_code >= 500:
            logger.error(
                "Shu server error",
                extra={
                    "error_id": error_id,
                    "error_code": exc.error_code,
                    "error_message": exc.message,
                    "details": exc.details,
                    "request_context": get_request_context(request),
                },
            )
        elif exc.status_code >= 400:
            # Log client errors (4xx) with basic info
            logger.warning(
                "Shu client error",
                extra={
                    "error_code": exc.error_code,
                    "error_message": exc.message,
                    "details": exc.details,
                    "request_context": get_request_context(request),
                },
            )

        # Prepare response
        error_response = {
            "error": {
                "code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
            }
        }

        # Add error ID for server errors
        if error_id:
            error_response["error"]["error_id"] = error_id

        return JSONResponse(
            status_code=exc.status_code,
            content=error_response,
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle HTTP exceptions with enhanced logging."""
        # Generate error ID for tracking (for 5xx errors)
        error_id = generate_error_id() if exc.status_code >= 500 else None

        # Log server errors (5xx) with full context
        if exc.status_code >= 500:
            log_exception_details(exc, request, error_id, include_traceback=settings.debug)
        elif exc.status_code >= 400:
            # Log client errors (4xx) with basic info
            logger.warning(
                "HTTP client error",
                extra={
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                    "request_context": get_request_context(request),
                },
            )

        # Prepare response
        error_response = {
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": exc.detail,
                "details": {},
            }
        }

        # Add error ID for server errors
        if error_id:
            error_response["error"]["error_id"] = error_id

        return JSONResponse(
            status_code=exc.status_code,
            content=error_response,
            headers=getattr(exc, "headers", None) or None,
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle general exceptions with enhanced logging and development mode support."""
        # Generate unique error ID for tracking
        error_id = generate_error_id()

        # Determine if we should include full traceback details
        include_traceback = settings.debug or settings.environment == "development" or settings.log_level == "DEBUG"

        # Log detailed exception information
        log_exception_details(exc, request, error_id, include_traceback)

        # Prepare response content
        error_response = {
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Internal server error",
                "error_id": error_id,
                "details": {},
            }
        }

        # In development mode, include additional debugging information
        if include_traceback:
            error_response["error"]["details"] = {
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "traceback": traceback.format_exc().split("\n"),
                "request_info": {
                    "method": request.method,
                    "path": request.url.path,
                    "query_params": dict(request.query_params),
                },
            }

            # Add a development warning
            error_response["error"]["development_mode"] = True
            error_response["error"]["warning"] = (
                "Detailed error information included for development. This will be hidden in production."
            )

        return JSONResponse(
            status_code=500,
            content=error_response,
        )


def setup_routes(app: FastAPI) -> None:
    """Configure application routes."""
    settings = get_settings_instance()

    # Authentication routes
    app.include_router(auth_router, prefix=settings.api_v1_prefix)

    # Configuration routes (public, no auth required)
    app.include_router(config_router, prefix=settings.api_v1_prefix)

    # Health check routes (comprehensive)
    app.include_router(health_router, prefix=settings.api_v1_prefix)
    app.include_router(system_router, prefix=settings.api_v1_prefix)

    # API routes
    app.include_router(knowledge_bases_router, prefix=settings.api_v1_prefix)
    app.include_router(resources_router, prefix=settings.api_v1_prefix)

    app.include_router(prompts_router, prefix=settings.api_v1_prefix)  # New generalized prompt system
    app.include_router(query_router, prefix=settings.api_v1_prefix)

    # LLM integration routes
    app.include_router(llm_router, prefix=settings.api_v1_prefix)
    app.include_router(model_configuration_router, prefix=settings.api_v1_prefix)

    # Chat integration routes
    app.include_router(chat_router, prefix=settings.api_v1_prefix)
    if settings.chat_plugins_enabled:
        app.include_router(chat_plugins_router, prefix=settings.api_v1_prefix)

    # Plugins routes
    app.include_router(plugins_router, prefix=settings.api_v1_prefix)

    # User preferences routes
    # Host auth routes (generic provider connection status)
    # Public alias for OAuth callback without API prefix (e.g., /auth/callback)
    app.include_router(host_auth_public_router)

    app.include_router(host_auth_router, prefix=settings.api_v1_prefix)

    app.include_router(user_preferences_router, prefix=settings.api_v1_prefix)
    app.include_router(branding_router, prefix=settings.api_v1_prefix)

    # RBAC management routes
    app.include_router(groups_router, prefix=settings.api_v1_prefix)
    app.include_router(permissions_router, prefix=settings.api_v1_prefix)
    app.include_router(user_permissions_router, prefix=settings.api_v1_prefix)

    # Experience Platform routes
    app.include_router(experiences_router, prefix=settings.api_v1_prefix)

    # Side-call routes
    app.include_router(side_call_router, prefix=settings.api_v1_prefix)

    # Compute known API router root paths for middleware normalization
    try:
        router_prefixes = [
            auth_router.prefix,
            config_router.prefix,
            health_router.prefix,
            system_router.prefix,
            knowledge_bases_router.prefix,
            resources_router.prefix,
            prompts_router.prefix,
            query_router.prefix,
            llm_router.prefix,
            model_configuration_router.prefix,
            chat_router.prefix,
            chat_plugins_router.prefix,
            plugins_router.prefix,
            user_preferences_router.prefix,
            host_auth_router.prefix,
            groups_router.prefix,
            permissions_router.prefix,
            user_permissions_router.prefix,
            experiences_router.prefix,
            side_call_router.prefix,
        ]
        base = settings.api_v1_prefix.rstrip("/")
        app.state.api_root_paths = set(base + p for p in router_prefixes)
        logger.info("Registered API router roots", extra={"api_root_paths": list(app.state.api_root_paths)})
    except Exception as e:
        logger.warning(f"Failed to compute API root paths: {e}")


def setup_event_handlers(app: FastAPI) -> None:
    """Configure startup and shutdown event handlers."""
    # Event handlers are now handled by the lifespan context manager
    pass


def custom_openapi():
    """Generate custom OpenAPI schema."""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=settings.app_name,
        version=settings.version,
        description="Shu - RAG backend and API",
        routes=app.routes,
    )

    # Add custom schema extensions
    openapi_schema["info"]["x-logo"] = {"url": "https://example.com/logo.png"}

    app.openapi_schema = openapi_schema
    return app.openapi_schema


# Ensure logging is configured as early as possible (before app instantiation)
# The lifespan will call setup_logging() again but it's guarded to no-op on second call
setup_logging()

# Create application instance
app = create_app()
app.openapi = custom_openapi
