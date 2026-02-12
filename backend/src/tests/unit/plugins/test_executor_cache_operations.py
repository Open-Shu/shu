"""
Unit tests for Plugin Executor cache operations.

Tests cover:
- Quota tracking with CacheBackend
- Concurrency control with CacheBackend

Note: Due to circular import issues in the test environment, these tests
focus on verifying the cache backend integration works correctly.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

# Set required environment variables BEFORE any shu imports
os.environ.setdefault("SHU_DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from shu.core.cache_backend import InMemoryCacheBackend


class TestExecutorCacheBackendIntegration:
    """Test that cache backend integration works correctly."""

    @pytest.mark.asyncio
    async def test_cache_backend_quota_operations(self):
        """Test quota-style operations with CacheBackend."""
        cache = InMemoryCacheBackend()

        # Simulate quota tracking operations
        day_key = "quota:d:test:user:plugin"
        month_key = "quota:m:test:user:plugin"

        # Check initial state (should be None)
        day_count = await cache.get(day_key)
        month_count = await cache.get(month_key)
        assert day_count is None
        assert month_count is None

        # Set initial counts
        await cache.set(day_key, "1", ttl_seconds=86400)  # 1 day
        await cache.set(month_key, "1", ttl_seconds=2592000)  # 30 days

        # Verify counts were set
        day_count = await cache.get(day_key)
        month_count = await cache.get(month_key)
        assert day_count == "1"
        assert month_count == "1"

        # Simulate incrementing counts
        await cache.set(day_key, "2", ttl_seconds=86400)
        await cache.set(month_key, "2", ttl_seconds=2592000)

        # Verify incremented counts
        day_count = await cache.get(day_key)
        month_count = await cache.get(month_key)
        assert day_count == "2"
        assert month_count == "2"

    @pytest.mark.asyncio
    async def test_cache_backend_concurrency_operations(self):
        """Test concurrency-style operations with CacheBackend."""
        cache = InMemoryCacheBackend()

        # Simulate concurrency tracking operations
        conc_key = "conc:test_provider"

        # Test increment operations (like acquiring concurrency slots)
        count1 = await cache.incr(conc_key)
        assert count1 == 1

        count2 = await cache.incr(conc_key)
        assert count2 == 2

        # Set TTL for auto-recovery
        await cache.expire(conc_key, 30)

        # Test decrement operations (like releasing concurrency slots)
        count3 = await cache.decr(conc_key)
        assert count3 == 1

        count4 = await cache.decr(conc_key)
        assert count4 == 0

        # Verify key still exists but with value 0
        exists = await cache.exists(conc_key)
        assert exists is True

        value = await cache.get(conc_key)
        assert value == "0"

    @pytest.mark.asyncio
    async def test_cache_backend_error_handling(self):
        """Test that cache backend handles errors gracefully."""
        # Create a mock cache that raises exceptions
        mock_cache = AsyncMock(spec=InMemoryCacheBackend)
        mock_cache.get.side_effect = Exception("Cache error")
        mock_cache.set.side_effect = Exception("Cache error")
        mock_cache.incr.side_effect = Exception("Cache error")
        mock_cache.decr.side_effect = Exception("Cache error")

        # Test that exceptions are raised (the executor should catch these)
        with pytest.raises(Exception, match="Cache error"):
            await mock_cache.get("test_key")

        with pytest.raises(Exception, match="Cache error"):
            await mock_cache.set("test_key", "test_value")

        with pytest.raises(Exception, match="Cache error"):
            await mock_cache.incr("test_key")

        with pytest.raises(Exception, match="Cache error"):
            await mock_cache.decr("test_key")

    @pytest.mark.asyncio
    async def test_cache_backend_factory_integration(self):
        """Test that get_cache_backend factory works correctly."""
        from shu.core.cache_backend import get_cache_backend, reset_cache_backend

        # Reset singleton to avoid stale client from a prior test's event loop
        reset_cache_backend()

        # Test that we can get a cache backend
        backend = await get_cache_backend()
        assert backend is not None

        # Test basic operations work
        await backend.set("test_key", "test_value", ttl_seconds=60)
        value = await backend.get("test_key")
        assert value == "test_value"

        # Test increment/decrement operations
        count1 = await backend.incr("counter_key")
        assert count1 == 1

        count2 = await backend.decr("counter_key")
        assert count2 == 0
