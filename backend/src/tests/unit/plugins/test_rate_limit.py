"""
Unit tests for plugins rate limiting.

Tests the TokenBucketLimiter used by plugins to ensure it works correctly
with the CacheBackend interface and fixed-window algorithm.
"""

import pytest
from unittest.mock import AsyncMock, patch
import os

# Set required environment variables BEFORE any shu imports
os.environ.setdefault("SHU_DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")


class TestPluginTokenBucketLimiter:
    """Tests for plugins TokenBucketLimiter with CacheBackend."""
    
    @pytest.fixture
    def mock_cache_backend(self):
        """Create a mock CacheBackend for testing plugin rate limiting."""
        cache = AsyncMock()
        cache._store = {}

        async def mock_get(key):
            """Return the current value stored at key, or None if not present."""
            return cache._store.get(key)

        async def mock_incr(key, amount=1):
            """Increment the integer value stored for key and return the updated value."""
            cache._store[key] = cache._store.get(key, 0) + amount
            return cache._store[key]

        async def mock_expire(key, seconds):
            """Simulate setting a TTL on a key."""
            return True

        cache.get = mock_get
        cache.incr = mock_incr
        cache.expire = mock_expire
        return cache
    
    @pytest.mark.asyncio
    async def test_allow_within_capacity(self, mock_cache_backend):
        """Requests within capacity are allowed."""
        from shu.plugins.rate_limit import TokenBucketLimiter
        
        with patch.object(TokenBucketLimiter, "_get_cache", return_value=mock_cache_backend):
            limiter = TokenBucketLimiter(
                namespace="test:plugin",
                capacity=5,
                refill_per_second=1,
            )
            
            allowed, retry_after = await limiter.allow(bucket="user:123")
            
            assert allowed is True
            assert retry_after == 0
    
    @pytest.mark.asyncio
    async def test_deny_over_capacity(self, mock_cache_backend):
        """Requests over capacity are denied."""
        from shu.plugins.rate_limit import TokenBucketLimiter

        with patch.object(TokenBucketLimiter, "_get_cache", return_value=mock_cache_backend):
            limiter = TokenBucketLimiter(
                namespace="test:plugin",
                capacity=2,
                refill_per_second=1,
            )

            # Exhaust the rate limit
            for _ in range(2):
                allowed, _ = await limiter.allow(bucket="user:123")
                assert allowed is True

            # Next request should be denied
            allowed, retry_after = await limiter.allow(bucket="user:123")

            assert allowed is False
            assert retry_after > 0

    @pytest.mark.asyncio
    async def test_per_call_overrides(self, mock_cache_backend):
        """Per-call capacity and refill rate overrides work correctly."""
        from shu.plugins.rate_limit import TokenBucketLimiter

        with patch.object(TokenBucketLimiter, "_get_cache", return_value=mock_cache_backend):
            limiter = TokenBucketLimiter(
                namespace="test:plugin",
                capacity=10,  # Default capacity
                refill_per_second=1,
            )

            # Use override capacity of 1
            allowed1, _ = await limiter.allow(
                bucket="user:123", 
                capacity=1, 
                refill_per_second=1
            )
            assert allowed1 is True

            # Second request with same override should be denied
            allowed2, retry_after = await limiter.allow(
                bucket="user:123", 
                capacity=1, 
                refill_per_second=1
            )
            assert allowed2 is False
            assert retry_after > 0

    @pytest.mark.asyncio
    async def test_cost_parameter(self, mock_cache_backend):
        """Cost parameter correctly consumes multiple tokens."""
        from shu.plugins.rate_limit import TokenBucketLimiter

        with patch.object(TokenBucketLimiter, "_get_cache", return_value=mock_cache_backend):
            limiter = TokenBucketLimiter(
                namespace="test:plugin",
                capacity=5,
                refill_per_second=1,
            )

            # Consume 3 tokens
            allowed1, _ = await limiter.allow(bucket="user:123", cost=3)
            assert allowed1 is True

            # Try to consume 3 more tokens (should fail, only 2 remaining)
            allowed2, retry_after = await limiter.allow(bucket="user:123", cost=3)
            assert allowed2 is False
            assert retry_after > 0

            # Consume 2 tokens (should succeed)
            allowed3, _ = await limiter.allow(bucket="user:123", cost=2)
            assert allowed3 is True

    @pytest.mark.asyncio
    async def test_error_handling_allows_request(self, mock_cache_backend):
        """Errors in cache operations allow the request (fail-open)."""
        from shu.plugins.rate_limit import TokenBucketLimiter

        # Make cache operations raise exceptions
        mock_cache_backend.get.side_effect = Exception("Cache error")
        mock_cache_backend.incr.side_effect = Exception("Cache error")

        with patch.object(TokenBucketLimiter, "_get_cache", return_value=mock_cache_backend):
            limiter = TokenBucketLimiter(
                namespace="test:plugin",
                capacity=1,
                refill_per_second=1,
            )

            # Should allow request despite cache errors
            allowed, retry_after = await limiter.allow(bucket="user:123")
            
            assert allowed is True
            assert retry_after == 0