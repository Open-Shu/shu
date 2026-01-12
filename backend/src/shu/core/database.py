"""
Database connection and session management for Shu RAG Backend.

This module provides database connection management, session handling,
and utilities for database operations.
"""

import os
import logging
from typing import AsyncGenerator, Generator, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from .exceptions import (
    DatabaseConnectionError, DatabaseInitializationError, DatabaseQueryError,
    DatabaseConstraintError, DatabaseTransactionError, DatabaseSessionError
)
from .logging import get_logger
import redis.asyncio as redis
from .config import get_settings_instance

logger = get_logger(__name__)

# Create declarative base
Base = declarative_base()

# Import User model to ensure it's available for SQLAlchemy relationship resolution
# This import ensures the User model is registered with SQLAlchemy's registry
try:
    from ..auth.models import User
    logger.debug("User model imported successfully for relationship resolution")
except ImportError as e:
    logger.warning(f"Could not import User model: {e}")

# Global async engine and session factory - lazy initialization
_async_engine = None
_AsyncSessionLocal = None


def get_database_url():
    env_url = os.getenv('SHU_DATABASE_URL')
    if env_url:
        logger.debug(f"Using database URL from environment variable: {env_url.split('@')[1] if '@' in env_url else 'URL format'}")
        return env_url
    try:
        from .config import get_settings_instance
        settings = get_settings_instance()
        logger.debug(f"Using database URL from settings: {settings.database_url.split('@')[1] if '@' in settings.database_url else 'URL format'}")
        return settings.database_url
    except Exception:
        logger.error("Could not determine database URL from environment or settings", exc_info=True)
        raise DatabaseConnectionError("Could not determine database URL. Please set SHU_DATABASE_URL environment variable or configure Settings properly.")


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
            parsed_url = database_url.replace('postgresql+asyncpg://', 'postgresql://')
            if '@' in parsed_url:
                # Extract non-sensitive parts for logging
                parts = parsed_url.split('@')
                if len(parts) == 2:
                    host_part = parts[1]
                    if '/' in host_part:
                        host_db = host_part.split('/')
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
            logger.error(f"Failed to create async database engine: {str(e)}")
            raise DatabaseConnectionError(f"engine creation: {str(e)}")
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
            logger.error(f"Failed to create async session factory: {str(e)}")
            raise DatabaseSessionError(f"session factory creation: {str(e)}")
    return _AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session_local = get_async_session_local()
    async with session_local() as session:
        try:
            yield session
        except SQLAlchemyError as e:
            logger.error(f"Database session error: {str(e)}")
            await session.rollback()
            raise DatabaseSessionError(f"session operation: {str(e)}")
        finally:
            await session.close()


async def get_db_session() -> AsyncSession:
    """Get a database session for background tasks."""
    session_local = get_async_session_local()
    return session_local()


async def init_db() -> None:
    try:
        # Ensure all models are imported so Base.metadata has all tables
        from ..models.registry import register_all_models  # noqa: F401
        register_all_models()
        engine = get_async_engine()

        # Log database initialization
        database_url = get_database_url()
        parsed_url = database_url.replace('postgresql+asyncpg://', 'postgresql://')
        if '@' in parsed_url:
            parts = parsed_url.split('@')
            if len(parts) == 2:
                host_part = parts[1]
                if '/' in host_part:
                    host_db = host_part.split('/')
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
        logger.error(f"Database initialization failed: {str(e)}")
        raise DatabaseSessionError(f"database initialization: {str(e)}")


async def close_db() -> None:
    try:
        engine = get_async_engine()
        await engine.dispose()
        logger.debug("Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database connections: {str(e)}")


async def check_db_connection() -> bool:
    """Check if database connection is working."""
    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database connection check failed: {str(e)}")
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
            logger.error(f"Transaction failed: {str(e)}")
            raise DatabaseSessionError(f"transaction execution: {str(e)}")
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
            logger.error(f"Async transaction failed: {str(e)}")
            raise DatabaseSessionError(f"async transaction execution: {str(e)}")
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
                "pool_size": pool.size() if hasattr(pool, 'size') else "unknown",
                "checked_in": pool.checkedin() if hasattr(pool, 'checkedin') else "unknown",
                "checked_out": pool.checkedout() if hasattr(pool, 'checkedout') else "unknown",
                "overflow": pool.overflow() if hasattr(pool, 'overflow') else "unknown",
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

# Redis client management - DEPRECATED
# Redis client management has been moved to cache_backend.py
# This function is kept for backward compatibility during migration.
# Use get_cache_backend() from cache_backend.py for new code.
# This will be removed in a future cleanup task (Task 13.2).

async def get_redis_client():
    """
    Get Redis client for caching and progress tracking.
    
    .. deprecated::
        This function is deprecated. Use `get_cache_backend()` from
        `shu.core.cache_backend` instead for cache operations.
        
        This function delegates to the cache_backend module's internal
        Redis client management. It will be removed in a future release.
    
    Note: InMemoryRedisClient fallback has been removed. Use get_cache_backend()
    for cache operations that work with both Redis and in-memory backends.
    
    Returns:
        An async Redis client instance.
    """
    from .cache_backend import _get_redis_client, CacheConnectionError
    
    settings = get_settings_instance()
    
    try:
        return await _get_redis_client()
    except CacheConnectionError as e:
        if settings.redis_required:
            logger.error("Redis is required but connection failed", extra={
                "redis_url": settings.redis_url,
                "error": str(e)
            })
            raise DatabaseConnectionError(
                f"Redis connection failed: {e}. "
                f"Please ensure Redis is running and accessible at {settings.redis_url}"
            ) from e
        
        if not settings.redis_fallback_enabled:
            logger.error("Redis fallback is disabled and Redis connection failed", extra={
                "redis_url": settings.redis_url,
                "error": str(e)
            })
            raise DatabaseConnectionError(
                f"Redis connection failed and fallback is disabled: {e}. "
                f"Please enable Redis fallback or ensure Redis is running at {settings.redis_url}"
            ) from e
        
        logger.error("Redis connection failed and InMemoryRedisClient has been removed", extra={
            "redis_url": settings.redis_url,
            "error": str(e)
        })
        
        # InMemoryRedisClient has been removed - use get_cache_backend() instead
        raise DatabaseConnectionError(
            f"Redis connection failed and InMemoryRedisClient fallback has been removed: {e}. "
            f"Please use get_cache_backend() from cache_backend.py instead."
        ) from e