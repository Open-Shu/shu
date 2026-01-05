"""
Unit tests for the rate limiting service.

Tests cover:
- RateLimitResult dataclass and headers generation
- TokenBucketRateLimiter with in-memory backend
- RateLimitService abstraction layer
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import os

# Set required environment variables BEFORE any shu imports
os.environ.setdefault("SHU_DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")


class TestRateLimitResult:
    """Tests for RateLimitResult dataclass."""
    
    def test_default_values(self):
        """RateLimitResult has sensible defaults."""
        from shu.core.rate_limiting import RateLimitResult
        
        result = RateLimitResult(allowed=True)
        assert result.allowed is True
        assert result.retry_after_seconds == 0
        assert result.remaining == 0
        assert result.limit == 0
        assert result.reset_seconds == 0
    
    def test_to_headers_allowed(self):
        """Headers for allowed request."""
        from shu.core.rate_limiting import RateLimitResult
        
        result = RateLimitResult(
            allowed=True,
            remaining=50,
            limit=100,
            reset_seconds=30,
        )
        headers = result.to_headers()
        
        assert headers["RateLimit-Limit"] == "100"
        assert headers["RateLimit-Remaining"] == "50"
        assert headers["RateLimit-Reset"] == "30"
        assert "Retry-After" not in headers
    
    def test_to_headers_denied(self):
        """Headers for denied request include Retry-After."""
        from shu.core.rate_limiting import RateLimitResult
        
        result = RateLimitResult(
            allowed=False,
            retry_after_seconds=10,
            remaining=0,
            limit=100,
            reset_seconds=10,
        )
        headers = result.to_headers()
        
        assert headers["Retry-After"] == "10"
        assert headers["RateLimit-Remaining"] == "0"


class TestTokenBucketRateLimiter:
    """Tests for TokenBucketRateLimiter."""
    
    @pytest.fixture
    def mock_redis(self):
        """Create a mock in-memory Redis client."""
        redis = AsyncMock()
        redis.__class__.__name__ = "InMemoryRedis"
        redis._store = {}
        
        async def mock_incr(key):
            redis._store[key] = redis._store.get(key, 0) + 1
            return redis._store[key]
        
        async def mock_expire(key, seconds):
            return True
        
        redis.incr = mock_incr
        redis.expire = mock_expire
        return redis
    
    @pytest.mark.asyncio
    async def test_check_allows_within_capacity(self, mock_redis):
        """Requests within capacity are allowed."""
        from shu.core.rate_limiting import TokenBucketRateLimiter
        
        with patch.object(TokenBucketRateLimiter, "_get_redis", return_value=mock_redis):
            limiter = TokenBucketRateLimiter(
                namespace="test",
                capacity=10,
                refill_per_second=1,
            )
            
            result = await limiter.check(key="user:123")
            
            assert result.allowed is True
            assert result.remaining >= 0
    
    @pytest.mark.asyncio
    async def test_check_denies_over_capacity(self, mock_redis):
        """Requests over capacity are denied after exceeding limit."""
        from shu.core.rate_limiting import TokenBucketRateLimiter

        with patch.object(TokenBucketRateLimiter, "_get_redis", return_value=mock_redis):
            limiter = TokenBucketRateLimiter(
                namespace="test",
                capacity=3,  # Small capacity for quick exhaustion
                refill_per_second=1,
            )

            # Exhaust the rate limit
            for _ in range(3):
                await limiter.check(key="user:123")

            # Next request should be denied
            result = await limiter.check(key="user:123")

            assert result.allowed is False
            assert result.retry_after_seconds > 0


class TestRateLimitService:
    """Tests for RateLimitService."""
    
    def test_service_initialization(self):
        """Service initializes with settings."""
        from shu.core.rate_limiting import RateLimitService
        
        mock_settings = MagicMock()
        mock_settings.enable_rate_limiting = True
        mock_settings.rate_limit_requests = 100
        mock_settings.rate_limit_period = 60
        mock_settings.strict_rate_limit_requests = 10
        mock_settings.llm_global_rate_limit = 100
        mock_settings.llm_global_tpm_limit = 100000
        
        service = RateLimitService(settings=mock_settings)
        
        assert service.enabled is True
    
    def test_service_disabled(self):
        """Service respects disabled setting."""
        from shu.core.rate_limiting import RateLimitService
        
        mock_settings = MagicMock()
        mock_settings.enable_rate_limiting = False
        
        service = RateLimitService(settings=mock_settings)
        
        assert service.enabled is False
    
    @pytest.mark.asyncio
    async def test_check_api_limit_disabled_allows(self):
        """Disabled service allows all requests."""
        from shu.core.rate_limiting import RateLimitService
        
        mock_settings = MagicMock()
        mock_settings.enable_rate_limiting = False
        
        service = RateLimitService(settings=mock_settings)
        result = await service.check_api_limit("user:123")
        
        assert result.allowed is True

