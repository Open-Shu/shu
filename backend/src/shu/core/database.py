"""Database connection and session management for Shu RAG Backend.

This module provides database connection management, session handling,
and utilities for database operations.
"""

import ast
import os
import re
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, declarative_base

from .config import DeploymentMode
from .exceptions import (
    DatabaseConnectionError,
    DatabaseSessionError,
)
from .logging import get_logger
from .tenant import CrossTenantInsertError, MissingTenantContextError, tenant_context

logger = get_logger(__name__)

# Create declarative base
Base = declarative_base()

# Note: User model import moved to register_all_models() to avoid circular imports
# The model will be registered when needed via the registry module

# Global async engine and session factory - lazy initialization
_async_engine = None
_AsyncSessionLocal = None


def get_database_url():
    env_url = os.getenv("SHU_DATABASE_URL")
    if env_url:
        logger.debug(
            f"Using database URL from environment variable: {env_url.split('@')[1] if '@' in env_url else 'URL format'}"
        )
        return env_url
    try:
        from .config import get_settings_instance

        settings = get_settings_instance()
        logger.debug(
            f"Using database URL from settings: {settings.database_url.split('@')[1] if '@' in settings.database_url else 'URL format'}"
        )
        return settings.database_url
    except Exception:
        logger.error("Could not determine database URL from environment or settings", exc_info=True)
        raise DatabaseConnectionError(
            "Could not determine database URL. Please set SHU_DATABASE_URL environment variable or configure Settings properly."
        )


def get_settings():
    try:
        from .config import get_settings_instance

        return get_settings_instance()
    except Exception:

        class MinimalSettings:
            database_pool_size = 20
            database_max_overflow = 30
            database_pool_timeout = 30
            database_pool_recycle = 3600
            debug = False
            use_pgbouncer = False
            # Defaulting to self_hosted keeps the fallback path safe: callers that
            # branch on deployment_mode get the most permissive non-tenant mode
            # rather than crashing on a missing attribute.
            deployment_mode = DeploymentMode.SELF_HOSTED
            # db_admin_url has no sensible fallback — the admin engine is
            # opt-in. Declared here so ``get_admin_engine`` can read the
            # attribute directly without defensive getattr.
            db_admin_url = None

        return MinimalSettings()


def get_async_engine():
    global _async_engine  # noqa: PLW0603 # This is currently working, so we'll leave it as is
    if _async_engine is None:
        try:
            database_url = get_database_url()
            settings = get_settings()

            # Log database configuration (without sensitive info)
            parsed_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
            if "@" in parsed_url:
                # Extract non-sensitive parts for logging
                parts = parsed_url.split("@")
                if len(parts) == 2:
                    host_part = parts[1]
                    if "/" in host_part:
                        host_db = host_part.split("/")
                        if len(host_db) == 2:
                            host_port = host_db[0]
                            database_name = host_db[1]
                            logger.debug(f"Database configuration: Host={host_port}, Database={database_name}")
                        else:
                            logger.debug(f"Database configuration: Host={host_part}")
                    else:
                        logger.debug(f"Database configuration: Host={host_part}")
                else:
                    logger.debug("Database configuration: URL format parsed")
            else:
                logger.debug(f"Database configuration: URL={parsed_url}")

            # PgBouncer in transaction mode reassigns connections between transactions. That means any new request may
            # end up on another host and then fail to load the cache. statement_cache_size=0 disables client-side caching
            # so each query falls back to unnamed statements.
            connect_args = {"statement_cache_size": 0} if settings.use_pgbouncer else {}

            _async_engine = create_async_engine(
                database_url,
                pool_size=settings.database_pool_size,
                max_overflow=settings.database_max_overflow,
                pool_timeout=settings.database_pool_timeout,
                pool_recycle=settings.database_pool_recycle,
                pool_pre_ping=True,
                echo=False,
                connect_args=connect_args,
            )
        except Exception as e:
            logger.error(f"Failed to create async database engine: {e!s}")
            raise DatabaseConnectionError(f"engine creation: {e!s}")
    return _async_engine


def get_async_session_local():
    global _AsyncSessionLocal  # noqa: PLW0603 # This is currently working, so we'll leave it as is
    if _AsyncSessionLocal is None:
        try:
            engine = get_async_engine()
            _AsyncSessionLocal = async_sessionmaker(
                bind=engine,
                expire_on_commit=False,
                autoflush=False,
                autocommit=False,
                class_=AsyncSession,
            )
        except Exception as e:
            logger.error(f"Failed to create async session factory: {e!s}")
            raise DatabaseSessionError(f"session factory creation: {e!s}")
    return _AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session_local = get_async_session_local()
    async with session_local() as session:
        try:
            yield session
        except SQLAlchemyError as e:
            logger.error(f"Database session error: {e!s}")
            await session.rollback()
            raise DatabaseSessionError(f"session operation: {e!s}")
        finally:
            await session.close()


# =============================================================================
# Tenant-isolation event hooks (SHU-761 Stage D)
#
# Three listeners wire `tenant_context` into the SQLAlchemy machinery:
#   - Engine "begin": stamps app.tenant_id on every new transaction via
#     set_config(..., true). Bind-parameter-safe; PgBouncer-transaction-mode-safe.
#   - Session "before_flush": auto-stamps tenant_id on new tenant-scoped
#     objects from the session's context; raises on mismatch.
#   - Engine "before_cursor_execute" (debug only): rejects raw SET on
#     app.tenant_id — the only legitimate path is through set_config in the
#     begin hook.
# =============================================================================


def _is_admin_connection(conn) -> bool:
    """Evaluate, true iff this connection belongs to the lazy admin engine.

    The begin listener below is registered class-level on ``Engine``, so it
    matches both ``_async_engine`` (shu_app, RLS-enforced) and
    ``_admin_engine`` (shu_admin, BYPASSRLS). For the admin path the
    ``set_config`` would be a no-op (BYPASSRLS ignores the GUC) but the
    round-trip still costs latency, and ``cross_tenant_query`` documents
    ``app.tenant_id`` as intentionally not set on admin sessions — a
    promise the code can actually keep by skipping the stamp here.

    Lazy-check ``_admin_engine`` because at import time it doesn't exist
    yet. ``AsyncEngine.sync_engine`` is what the listener's ``conn.engine``
    matches against.
    """
    if _admin_engine is None:
        return False
    return conn.engine is _admin_engine.sync_engine


@event.listens_for(Engine, "begin")
def _set_tenant_on_begin(conn) -> None:
    # set_config is Postgres-specific. SQLite-backed unit-test sessions hit
    # this hook too and would fail with "no such function: set_config" —
    # gate the whole hook on the dialect, not just the missing-context log.
    if conn.dialect.name != "postgresql":
        return
    # Admin engine bypasses RLS — stamping the GUC there is wasted work.
    if _is_admin_connection(conn):
        return
    tid = tenant_context.get(None)
    if not tid:
        # Falsy (None or "") => no tenant. Never write an empty string into the
        # GUC: ``current_setting('app.tenant_id', true)`` then returns '' instead
        # of NULL, and the RLS policy's ``::uuid`` cast raises on '' (vs. NULL,
        # which is harmless). Leaving it unset means default-deny returns 0 rows.
        # Log once at DEBUG so the missing-context site is greppable without
        # spamming production logs.
        logger.debug("transaction begun without tenant context")
        return
    conn.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tid)},
    )


def _is_tenant_scoped(obj: object) -> bool:
    """Check duck-typed: does this ORM object map to a tenant-scoped table.

    Avoids importing ``models.base.TenantScopedMixin`` here — that would
    close a circular import (models.base imports Base from this module).
    Looking for the column instead is equivalent: every tenant-scoped table
    has a ``tenant_id`` column by definition (the mixin declares it), and
    no global table has one.
    """
    table = getattr(type(obj), "__table__", None)
    return table is not None and "tenant_id" in table.columns


@event.listens_for(Session, "before_flush")
def _stamp_tenant_id(session, flush_context, instances) -> None:
    tid = tenant_context.get(None)

    # session.new — auto-stamp from context, or raise on mismatch / missing context.
    for obj in session.new:
        if not _is_tenant_scoped(obj):
            continue
        current = getattr(obj, "tenant_id", None)
        cls_name = type(obj).__name__
        if current is None:
            if tid is None:
                raise MissingTenantContextError(
                    f"Cannot flush {cls_name}: tenant_id not set and tenant_context is empty. "
                    "Ensure the route has a tenant-resolution dependency or the worker handler "
                    "is wrapped to set tenant_context for the job."
                )
            obj.tenant_id = tid
        elif tid is not None and current != tid:
            raise CrossTenantInsertError(f"{cls_name}.tenant_id = {current!r} does not match session context {tid!r}")

    # session.dirty — catch UPDATE-time cross-tenant mismatches the same way
    # as inserts. Loading an object under tenant X then mutating its
    # tenant_id to Y is almost always a bug; the RLS WITH CHECK policy would
    # reject the UPDATE at the DB layer anyway, but raising here gives a
    # stack trace pointing at the mutation site instead of a generic
    # row-rejected error at flush. UPDATEs don't auto-stamp (we never
    # silently change tenant_id on an existing row); they only validate.
    for obj in session.dirty:
        if not _is_tenant_scoped(obj):
            continue
        current = getattr(obj, "tenant_id", None)
        # ``current is None`` on a dirty object means the row's tenant_id was
        # explicitly cleared, which is also a write that RLS would reject —
        # surface it as a mismatch with the session's context.
        if tid is not None and current != tid:
            raise CrossTenantInsertError(
                f"{type(obj).__name__}.tenant_id = {current!r} does not match session context " f"{tid!r} (update path)"
            )


# Compiled once at import — cheap to match against every statement and the
# guard is the only intended interceptor of `app.tenant_id` SETs anyway.
_SET_TENANT_RE = re.compile(
    r"^\s*SET\s+(?:SESSION\s+|LOCAL\s+)?app\.tenant_id\b",
    re.IGNORECASE,
)


@event.listens_for(Engine, "before_cursor_execute")
def _reject_unsafe_set(conn, cursor, statement, parameters, context, executemany) -> None:
    # The check is dev/test/CI only — production runs with debug=False, so the
    # function returns immediately. There is no legitimate use of raw SET on
    # app.tenant_id; the begin hook is the single point of enforcement.
    # get_settings() is already cached at the Settings layer, so no need for
    # a separate module-level cache here (and a module global would make
    # tests brittle without buying measurable perf).
    if not get_settings().debug:
        return
    if _SET_TENANT_RE.match(statement):
        raise RuntimeError(
            f"Direct SET on app.tenant_id is forbidden — use set_config(..., true) via "
            f"the engine begin hook. Got: {statement!r}"
        )


# =============================================================================
# Admin engine + session factory (SHU-761 Stage D, task 8.5)
#
# Parallel to the app's lazy engine but bound to SHU_DB_ADMIN_URL (the
# shu_admin role). Used only by TenantAdminService and (eventually) the
# Alembic env.py rotation. shu_admin has BYPASSRLS, so this engine intentionally
# does not participate in the per-request tenant context dance.
# =============================================================================

_admin_engine = None
_AdminSessionLocal = None


def get_admin_engine():
    global _admin_engine  # noqa: PLW0603
    if _admin_engine is None:
        settings = get_settings()
        admin_url = settings.db_admin_url
        if not admin_url:
            raise DatabaseConnectionError(
                "SHU_DB_ADMIN_URL is not configured. The admin engine is only valid "
                "after Stage C roles land and the operator populates the env var."
            )
        connect_args = {"statement_cache_size": 0} if settings.use_pgbouncer else {}
        _admin_engine = create_async_engine(
            admin_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            pool_recycle=settings.database_pool_recycle,
            pool_pre_ping=True,
            echo=False,
            connect_args=connect_args,
        )
    return _admin_engine


def get_admin_session_local():
    global _AdminSessionLocal  # noqa: PLW0603
    if _AdminSessionLocal is None:
        _AdminSessionLocal = async_sessionmaker(
            bind=get_admin_engine(),
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
            class_=AsyncSession,
        )
    return _AdminSessionLocal


async def get_db_session() -> AsyncSession:
    """Get a database session for background tasks."""
    session_local = get_async_session_local()
    return session_local()


def _read_revision_identifiers(source: str) -> tuple[str | None, tuple[str, ...]]:
    """Return (revision, parents) parsed from module-level assignments.

    `revision` is a single string. `down_revision` is normalised to a tuple of
    parent ids — empty for the base, single-element for a normal migration,
    multi-element for an alembic merge migration.
    """
    tree = ast.parse(source)
    revision: str | None = None
    parents: tuple[str, ...] = ()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
            value_expr: ast.expr = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value_expr = node.value
        else:
            continue
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        try:
            value = ast.literal_eval(value_expr)
        except (ValueError, SyntaxError):
            continue
        if "revision" in names and isinstance(value, str):
            revision = value
        if "down_revision" in names:
            if value is None:
                parents = ()
            elif isinstance(value, str):
                parents = (value,)
            elif isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):
                parents = tuple(value)
    return revision, parents


def _resolve_alembic_head() -> str:
    """Find the head revision in the bundled migrations/versions/ tree.

    Reads each file with `ast.parse` + `ast.literal_eval` rather than importing
    it. Migration modules can have side-effecting imports (e.g.
    `from migrations.seed_data...`) that only resolve under alembic's runtime
    sys.path setup; importing them at app startup would couple shu-api to
    alembic's CLI conventions. AST parsing also correctly handles merge
    migrations where `down_revision` is a tuple of parents.
    """
    from pathlib import Path

    candidates = [
        Path("/app/migrations/versions"),
        Path(__file__).resolve().parents[3] / "migrations" / "versions",
    ]
    versions_dir = next((p for p in candidates if p.is_dir()), None)
    if versions_dir is None:
        raise DatabaseSessionError(f"migrations/versions/ not found; looked in {[str(p) for p in candidates]}")

    revisions: dict[str, tuple[str, ...]] = {}
    for path in versions_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        revision, parents = _read_revision_identifiers(path.read_text(encoding="utf-8"))
        if revision is None:
            continue
        revisions[revision] = parents

    if not revisions:
        raise DatabaseSessionError(f"no migration files found under {versions_dir}")

    pointed_to = {parent for parents in revisions.values() for parent in parents}
    heads = [r for r in revisions if r not in pointed_to]
    if len(heads) != 1:
        raise DatabaseSessionError(
            f"expected exactly one alembic head; found {heads}. " "Migrations have branched and need to be reconciled."
        )
    return heads[0]


async def verify_schema_version() -> None:
    """Raise DatabaseSessionError unless alembic_version matches the bundled head."""
    expected = _resolve_alembic_head()

    engine = get_async_engine()
    async with engine.begin() as conn:
        try:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
        except SQLAlchemyError as e:
            raise DatabaseSessionError(
                "alembic_version table missing — migrations have not run. "
                "Run the shu-db-migrate Job (hosted) or `make migrate-local-lab` (dev) "
                "before starting shu-api."
            ) from e

    if row is None or row[0] is None:
        raise DatabaseSessionError(
            "alembic_version row missing — migrations have not run. "
            "Run the shu-db-migrate Job (hosted) or `make migrate-local-lab` (dev)."
        )

    current = row[0]
    if current != expected:
        raise DatabaseSessionError(
            f"schema at revision {current!r}, code expects {expected!r}. "
            "Run migrations (shu-db-migrate Job in hosted, `make migrate-local-lab` in dev) "
            "before starting shu-api."
        )

    logger.info("Schema verified at alembic revision %s", current)


async def close_db() -> None:
    # Two engines may have been lazily built: the app engine (every process)
    # and the admin engine (only when an admin-tooling code path actually
    # called get_admin_engine). Dispose whichever was built so neither pool
    # outlives the process shutdown. Reading the module globals directly
    # avoids accidentally building the admin engine just to tear it down.
    global _admin_engine, _AdminSessionLocal  # noqa: PLW0603

    try:
        engine = get_async_engine()
        await engine.dispose()
    except Exception as e:
        logger.error(f"Error closing app engine: {e!s}")

    if _admin_engine is not None:
        try:
            await _admin_engine.dispose()
        except Exception as e:
            logger.error(f"Error closing admin engine: {e!s}")
        _admin_engine = None
        _AdminSessionLocal = None

    logger.debug("Database connections closed")


async def check_db_connection() -> bool:
    """Check if database connection is working."""
    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database connection check failed: {e!s}")
        return False


class DatabaseManager:
    """Database manager for handling connections and transactions."""

    def __init__(self) -> None:
        self.engine = get_async_engine()
        self.SessionLocal = get_async_session_local()

    def get_session(self) -> AsyncSession:
        """Get a new database session."""
        return self.SessionLocal()

    async def execute_transaction(self, func, *args: Any, **kwargs: Any):
        """Execute a function within a database transaction."""
        db = self.get_session()
        try:
            result = await func(db, *args, **kwargs)
            await db.commit()
            return result
        except Exception as e:
            await db.rollback()
            logger.error(f"Transaction failed: {e!s}")
            raise DatabaseSessionError(f"transaction execution: {e!s}")
        finally:
            await db.close()

    async def execute_async_transaction(self, func, *args: Any, **kwargs: Any):
        """Execute an async function within a database transaction."""
        db = self.get_session()
        try:
            result = await func(db, *args, **kwargs)
            await db.commit()
            return result
        except Exception as e:
            await db.rollback()
            logger.error(f"Async transaction failed: {e!s}")
            raise DatabaseSessionError(f"async transaction execution: {e!s}")
        finally:
            await db.close()

    async def health_check(self) -> dict:
        """Perform database health check."""
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

            # Get pool status
            pool = self.engine.pool
            return {
                "status": "healthy",
                "pool_size": pool.size() if hasattr(pool, "size") else "unknown",
                "checked_in": pool.checkedin() if hasattr(pool, "checkedin") else "unknown",
                "checked_out": pool.checkedout() if hasattr(pool, "checkedout") else "unknown",
                "overflow": pool.overflow() if hasattr(pool, "overflow") else "unknown",
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }


# Global database manager instance - lazy initialization
_db_manager = None


def get_db_manager() -> DatabaseManager:
    """Get or create the database manager instance."""
    global _db_manager  # noqa: PLW0603 # This is currently working, so we'll leave it as is
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


class _DatabaseManagerProxy:
    def __getattr__(self, name):  # noqa: ANN204 # not sure what this returns
        """Get attribute handler."""
        return getattr(get_db_manager(), name)

    def __call__(self, *args: Any, **kwargs: Any):  # noqa: ANN204 # not sure what this returns
        """Call handler."""
        return get_db_manager()(*args, **kwargs)


# For backward compatibility
db_manager = _DatabaseManagerProxy()
