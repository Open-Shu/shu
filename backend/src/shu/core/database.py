"""Database connection and session management for Shu RAG Backend.

This module provides database connection management, session handling,
and utilities for database operations.
"""

import os
import re
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from .exceptions import (
    DatabaseConnectionError,
    DatabaseSessionError,
)
from .logging import get_logger

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


async def get_db_session() -> AsyncSession:
    """Get a database session for background tasks."""
    session_local = get_async_session_local()
    return session_local()


_REVISION_RE = re.compile(r'^revision\s*[:=]\s*[\'"]([^\'"]+)[\'"]', re.MULTILINE)
_DOWN_REVISION_RE = re.compile(r'^down_revision\s*[:=]\s*[\'"]([^\'"]+)[\'"]', re.MULTILINE)


def _resolve_alembic_head() -> str:
    """Find the head revision in the bundled migrations/versions/ tree.

    Regex-parses each file's `revision` and `down_revision` strings rather
    than importing the modules. Migration modules can have side-effecting
    imports (e.g. `from migrations.seed_data...`) that only resolve under
    alembic's runtime sys.path setup; importing them at app startup would
    couple shu-api to alembic's CLI conventions.
    """
    from pathlib import Path

    candidates = [
        Path("/app/migrations/versions"),
        Path(__file__).resolve().parents[3] / "migrations" / "versions",
    ]
    versions_dir = next((p for p in candidates if p.is_dir()), None)
    if versions_dir is None:
        raise DatabaseSessionError(f"migrations/versions/ not found; looked in {[str(p) for p in candidates]}")

    revisions: dict[str, str | None] = {}
    for path in versions_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        text_ = path.read_text()
        rev = _REVISION_RE.search(text_)
        if rev is None:
            continue
        down = _DOWN_REVISION_RE.search(text_)
        revisions[rev.group(1)] = down.group(1) if down else None

    if not revisions:
        raise DatabaseSessionError(f"no migration files found under {versions_dir}")

    pointed_to = {d for d in revisions.values() if d is not None}
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
    try:
        engine = get_async_engine()
        await engine.dispose()
        logger.debug("Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database connections: {e!s}")


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
