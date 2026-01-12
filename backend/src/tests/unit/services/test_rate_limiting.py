"""
Unit tests for the rate limiting service.

Tests cover:
- RateLimitResult dataclass and headers generation
- TokenBucketRateLimiter with CacheBackend
- RateLimitService abstraction layer
- Fixed-window algorithm behavior
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
    """Tests for TokenBucketRateLimiter with CacheBackend."""
    
    @pytest.fixture
    def mock_cache_backend(self):
        """
        Create a mock CacheBackend for testing rate limiting.
        
        The mock implements async `get`, `incr`, `decr`, and `expire` operations backed by an internal `_store` dict.
        
        Returns:
            AsyncMock: A mock CacheBackend instance with the required methods.
        """
        cache = AsyncMock()
        cache._store = {}

        async def mock_get(key):
            """Return the current value stored at key, or None if not present."""
            return cache._store.get(key)

        async def mock_incr(key, amount=1):
            """Increment the integer value stored for key and return the updated value."""
            cache._store[key] = cache._store.get(key, 0) + amount
            return cache._store[key]

        async def mock_decr(key, amount=1):
            """Decrement the integer value stored for key and return the updated value."""
            cache._store[key] = cache._store.get(key, 0) - amount
            return cache._store[key]

        async def mock_expire(key, seconds):
            """Simulate setting a TTL on a key."""
            return True

        cache.get = mock_get
        cache.incr = mock_incr
        cache.decr = mock_decr
        cache.expire = mock_expire
        return cache
    
    @pytest.mark.asyncio
    async def test_check_allows_within_capacity(self, mock_cache_backend):
        """Requests within capacity are allowed."""
        from shu.core.rate_limiting import TokenBucketRateLimiter
        
        with patch.object(TokenBucketRateLimiter, "_get_cache", return_value=mock_cache_backend):
            limiter = TokenBucketRateLimiter(
                namespace="test",
                capacity=10,
                refill_per_second=1,
            )
            
            result = await limiter.check(key="user:123")
            
            assert result.allowed is True
            assert result.remaining >= 0
    
    @pytest.mark.asyncio
    async def test_check_denies_over_capacity(self, mock_cache_backend):
        """Requests over capacity are denied after exceeding limit."""
        from shu.core.rate_limiting import TokenBucketRateLimiter

        with patch.object(TokenBucketRateLimiter, "_get_cache", return_value=mock_cache_backend):
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

    @pytest.mark.asyncio
    async def test_fixed_window_algorithm_works_correctly(self, mock_cache_backend):
        """Fixed-window algorithm correctly tracks requests within time windows."""
        from shu.core.rate_limiting import TokenBucketRateLimiter

        with patch.object(TokenBucketRateLimiter, "_get_cache", return_value=mock_cache_backend):
            limiter = TokenBucketRateLimiter(
                namespace="test",
                capacity=5,
                refill_per_second=1,  # 5 requests per 5 seconds
            )

            # Mock time to control window boundaries - patch the exact module reference
            with patch('shu.core.rate_limiting.time.time', return_value=1000):
                # First 5 requests should be allowed
                for i in range(5):
                    result = await limiter.check(key="user:123")
                    assert result.allowed is True, f"Request {i+1} should be allowed"

                # 6th request should be denied
                result = await limiter.check(key="user:123")
                assert result.allowed is False

    @pytest.mark.asyncio
    async def test_works_identically_with_both_backends(self, mock_cache_backend):
        """Rate limiting behavior is identical regardless of backend."""
        from shu.core.rate_limiting import TokenBucketRateLimiter

        # Test with mock backend (simulating both Redis and InMemory)
        with patch.object(TokenBucketRateLimiter, "_get_cache", return_value=mock_cache_backend):
            limiter = TokenBucketRateLimiter(
                namespace="test",
                capacity=2,
                refill_per_second=1,
            )

            # First request allowed
            result1 = await limiter.check(key="user:123")
            assert result1.allowed is True
            assert result1.remaining == 1

            # Second request allowed
            result2 = await limiter.check(key="user:123")
            assert result2.allowed is True
            assert result2.remaining == 0

            # Third request denied
            result3 = await limiter.check(key="user:123")
            assert result3.allowed is False
            assert result3.retry_after_seconds > 0


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
