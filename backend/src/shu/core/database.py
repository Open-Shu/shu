"""Database connection and session management for Shu RAG Backend.

This module provides database connection management, session handling,
and utilities for database operations.
"""

import os
from collections.abc import AsyncGenerator

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

        return MinimalSettings()


def get_async_engine():
    global _async_engine
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

            _async_engine = create_async_engine(
                database_url,
                pool_size=settings.database_pool_size,
                max_overflow=settings.database_max_overflow,
                pool_timeout=settings.database_pool_timeout,
                pool_recycle=settings.database_pool_recycle,
                pool_pre_ping=True,
                echo=False,
            )
        except Exception as e:
            logger.error(f"Failed to create async database engine: {e!s}")
            raise DatabaseConnectionError(f"engine creation: {e!s}")
    return _async_engine


def get_async_session_local():
    global _AsyncSessionLocal
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


async def init_db() -> None:
    try:
        # Ensure all models are imported so Base.metadata has all tables
        from ..models.registry import register_all_models

        register_all_models()
        engine = get_async_engine()

        # Log database initialization
        database_url = get_database_url()
        parsed_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        if "@" in parsed_url:
            parts = parsed_url.split("@")
            if len(parts) == 2:
                host_part = parts[1]
                if "/" in host_part:
                    host_db = host_part.split("/")
                    if len(host_db) == 2:
                        host_port = host_db[0]
                        database_name = host_db[1]
                        logger.debug(f"Initializing database tables: Host={host_port}, Database={database_name}")
                    else:
                        logger.debug(f"Initializing database tables: Host={host_part}")
                else:
                    logger.debug(f"Initializing database tables: Host={host_part}")
            else:
                logger.debug("Initializing database tables")
        else:
            logger.debug("Initializing database tables")

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e!s}")
        raise DatabaseSessionError(f"database initialization: {e!s}")


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

    def __init__(self):
        self.engine = get_async_engine()
        self.SessionLocal = get_async_session_local()

    def get_session(self) -> AsyncSession:
        """Get a new database session."""
        return self.SessionLocal()

    async def execute_transaction(self, func, *args, **kwargs):
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

    async def execute_async_transaction(self, func, *args, **kwargs):
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


def get_db_manager():
    """Get or create the database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


class _DatabaseManagerProxy:
    def __getattr__(self, name):
        return getattr(get_db_manager(), name)

    def __call__(self, *args, **kwargs):
        return get_db_manager()(*args, **kwargs)


# For backward compatibility
db_manager = _DatabaseManagerProxy()
