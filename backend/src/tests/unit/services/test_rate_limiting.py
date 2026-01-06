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


class TestGetClientIp:
    """Tests for get_client_ip utility function."""

    def test_extracts_from_forwarded_header(self):
        """Extracts first IP from X-Forwarded-For."""
        from shu.core.rate_limiting import get_client_ip

        headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
        result = get_client_ip(headers, "fallback")

        assert result == "1.2.3.4"

    def test_falls_back_to_client_host(self):
        """Falls back to client_host when no header."""
        from shu.core.rate_limiting import get_client_ip

        headers = {}
        result = get_client_ip(headers, "10.0.0.1")

        assert result == "10.0.0.1"

    def test_returns_unknown_when_no_info(self):
        """Returns 'unknown' when no info available."""
        from shu.core.rate_limiting import get_client_ip

        result = get_client_ip({}, None)

        assert result == "unknown"


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
        """
        Create a simple in-memory mock of an async Redis client for tests.
        
        The mock implements async `incr(key)`, `incrby(key, amount)`, and `expire(key, seconds)` operations backed by an internal `_store` dict:
        - `incr` increments the integer value for `key` by 1 and returns the new value.
        - `incrby` increments the integer value for `key` by `amount` and returns the new value.
        - `expire` always returns `True`.
        
        Returns:
            AsyncMock: An AsyncMock instance named `InMemoryRedis` with `_store` and the above async methods.
        """
        redis = AsyncMock()
        redis.__class__.__name__ = "InMemoryRedis"
        redis._store = {}

        async def mock_incr(key):
            """
            Increment the integer value stored for `key` in the in-memory mock Redis and return the updated value.
            
            Parameters:
                key (str): The Redis key whose integer value should be incremented.
            
            Returns:
                int: The new integer value stored at `key` after incrementing.
            """
            redis._store[key] = redis._store.get(key, 0) + 1
            return redis._store[key]

        async def mock_incrby(key, amount):
            """
            Increment a mocked Redis key by a given amount and return the updated value.
            
            Parameters:
                key (str): The Redis key to increment.
                amount (int): The amount to add to the key's current integer value.
            
            Returns:
                int: The key's new integer value after the increment.
            """
            redis._store[key] = redis._store.get(key, 0) + amount
            return redis._store[key]

        async def mock_expire(key, seconds):
            """
            Simulate setting a time-to-live on a key in an async-compatible test double.
            
            Parameters:
                key (str): The key to set an expiration for.
                seconds (int): Time-to-live in seconds.
            
            Returns:
                bool: `True` indicating the expiration was (mock) applied.
            """
            return True

        async def mock_get(key):
            """
            Return the current value stored at key, or None if not present.
            
            Parameters:
                key (str): The Redis key to retrieve.
            
            Returns:
                The value stored at key, or None if not present.
            """
            return redis._store.get(key)

        redis.incr = mock_incr
        redis.incrby = mock_incrby
        redis.expire = mock_expire
        redis.get = mock_get
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


class TestProviderRateLimits:
    """Tests for per-provider rate limiting (no global limits)."""

    @pytest.mark.asyncio
    async def test_check_llm_rpm_with_provider_limit(self):
        """Per-provider RPM limits use provider-specific bucket."""
        from shu.core.rate_limiting import RateLimitService

        # Create mock settings - no global LLM limits needed
        mock_settings = MagicMock()
        mock_settings.enable_rate_limiting = True

        service = RateLimitService(settings=mock_settings)

        # Check with provider-specific limit (required, not optional)
        result = await service.check_llm_rpm_limit(
            user_id="user1",
            provider_id="provider_openai",
            rpm_override=30
        )

        assert result.allowed is True
        assert result.limit == 30

    @pytest.mark.asyncio
    async def test_check_llm_tpm_with_provider_limit(self):
        """Per-provider TPM limits use provider-specific bucket."""
        from shu.core.rate_limiting import RateLimitService

        # Create mock settings - no global LLM limits needed
        mock_settings = MagicMock()
        mock_settings.enable_rate_limiting = True

        service = RateLimitService(settings=mock_settings)

        # Check with provider-specific TPM limit (required, not optional)
        result = await service.check_llm_tpm_limit(
            user_id="user1",
            token_cost=500,
            provider_id="provider_anthropic",
            tpm_override=10000
        )

        assert result.allowed is True
        assert result.limit == 10000

    @pytest.mark.asyncio
    async def test_zero_limit_means_no_check_at_streaming_layer(self):
        """
        Documents that a provider rate limit of 0 is handled by the streaming layer and not by the RateLimitService.
        
        The streaming layer (_check_provider_rate_limits) short-circuits and skips calling the service when a provider's configured limit is 0; the service itself expects positive limits and does not perform this zero-limit short-circuiting.
        """
        # This is a documentation test - actual skipping is in chat_streaming.py
        # When limit is 0, _check_provider_rate_limits returns early
        pass


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
        # Note: LLM rate limits are per-provider, not global

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
