"""
Rate limiting service for Shu.

Provides a unified rate limiting interface with Redis and in-memory backends.
Supports both RPM (requests per minute) and TPM (tokens per minute) limiting.

Design follows SOLID principles:
- Single Responsibility: Each class has one purpose
- Open/Closed: New limiters can be added without modifying existing code
- Liskov Substitution: All limiters implement the same protocol
- Interface Segregation: Minimal interface for rate limiting
- Dependency Inversion: Consumers depend on abstractions, not concrete implementations
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

logger = logging.getLogger(__name__)


def get_client_ip(headers: Dict[str, str], client_host: Optional[str] = None) -> str:
    """Extract client IP from request headers.

    Checks X-Forwarded-For for proxied requests, falls back to client host.

    Args:
        headers: Request headers (or dict-like with .get())
        client_host: Direct client host if available

    Returns:
        Client IP address string
    """
    forwarded = headers.get("X-Forwarded-For") if hasattr(headers, "get") else None
    if forwarded:
        return forwarded.split(",")[0].strip()
    return client_host or "unknown"


@dataclass(frozen=True)
class RateLimitResult:
    """Result of a rate limit check."""
    allowed: bool
    retry_after_seconds: int = 0
    remaining: int = 0
    limit: int = 0
    reset_seconds: int = 0

    def to_headers(self) -> dict[str, str]:
        """Generate standard rate limit response headers."""
        headers = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(max(0, self.remaining)),
            "RateLimit-Reset": str(self.reset_seconds),
        }
        if not self.allowed:
            headers["Retry-After"] = str(self.retry_after_seconds)
        return headers


class RateLimiter(Protocol):
    """Protocol for rate limiters."""
    
    async def check(
        self,
        key: str,
        cost: int = 1,
        capacity: Optional[int] = None,
        refill_per_second: Optional[int] = None,
    ) -> RateLimitResult:
        """Check if a request is allowed and consume quota if so.
        
        Args:
            key: Unique identifier for the rate limit bucket (e.g., user_id, ip)
            cost: Number of tokens to consume (1 for RPM, token count for TPM)
            capacity: Override default capacity
            refill_per_second: Override default refill rate
            
        Returns:
            RateLimitResult with allowed status and metadata
        """
        ...


# Lua script for token bucket: refill then try to consume tokens.
# KEYS[1]=bucket_key, ARGV[1]=now_ms, ARGV[2]=capacity, ARGV[3]=refill_tokens_per_ms, ARGV[4]=cost
TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local rate = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  ts = now
else
  local delta = math.max(0, now - ts)
  tokens = math.min(capacity, tokens + (delta * rate))
  ts = now
end
local allowed = 0
local retry_ms = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  if rate > 0 then
    retry_ms = math.ceil((cost - tokens) / rate)
  else
    retry_ms = 1000
  end
end
redis.call('HMSET', key, 'tokens', tokens, 'ts', ts)
-- set TTL to ~two windows worth to allow cleanup
local ttl = 2000
if rate > 0 then
  ttl = math.ceil((capacity / rate) * 2)
end
redis.call('PEXPIRE', key, ttl)
return {allowed, math.ceil(tokens), retry_ms, capacity}
"""


class TokenBucketRateLimiter:
    """Token bucket rate limiter with Redis backend and in-memory fallback.
    
    Uses atomic Lua script for Redis, fixed-window fallback for in-memory.
    """
    
    def __init__(
        self,
        namespace: str = "rl",
        capacity: int = 60,
        refill_per_second: int = 1,
    ):
        """Initialize rate limiter.
        
        Args:
            namespace: Redis key namespace (e.g., "rl:api", "rl:auth")
            capacity: Maximum tokens in bucket (burst capacity)
            refill_per_second: Tokens added per second (sustained rate)
        """
        self.namespace = namespace
        self.capacity = max(1, int(capacity))
        self.refill_per_second = max(1, int(refill_per_second))
        self._redis: Optional[Any] = None
    
    async def _get_redis(self) -> Any:
        """Get Redis client lazily."""
        if self._redis is None:
            from .database import get_redis_client
            self._redis = await get_redis_client()
        return self._redis
    
    def _key(self, bucket: str) -> str:
        """Generate Redis key for bucket."""
        return f"{self.namespace}:{bucket}"
    
    @staticmethod
    def _is_in_memory(redis_client: Any) -> bool:
        """Check if using in-memory fallback."""
        clsname = redis_client.__class__.__name__
        return "InMemory" in clsname or "Fake" in clsname

    async def check(
        self,
        key: str,
        cost: int = 1,
        capacity: Optional[int] = None,
        refill_per_second: Optional[int] = None,
    ) -> RateLimitResult:
        """Check if request is allowed and consume quota.

        Args:
            key: Unique identifier for rate limit bucket
            cost: Tokens to consume (1 for RPM, token count for TPM)
            capacity: Override default capacity
            refill_per_second: Override default refill rate

        Returns:
            RateLimitResult with allowed status and headers
        """
        redis = await self._get_redis()
        now_ms = int(time.time() * 1000)
        bucket_key = self._key(key)
        cap = max(1, int(capacity if capacity is not None else self.capacity))
        rps = max(1, int(refill_per_second if refill_per_second is not None else self.refill_per_second))
        rate_per_ms = float(rps) / 1000.0

        # In-memory fallback: fixed-window
        if self._is_in_memory(redis):
            return await self._check_in_memory(redis, bucket_key, cost, cap, rps)

        # Redis: atomic token bucket via Lua
        return await self._check_redis(redis, bucket_key, now_ms, cost, cap, rate_per_ms, rps)

    async def _check_in_memory(
        self,
        redis: Any,
        bucket_key: str,
        cost: int,
        capacity: int,
        refill_per_second: int,
    ) -> RateLimitResult:
        """Fixed-window rate limiting for in-memory backend."""
        window_s = max(1, int(capacity / max(1, refill_per_second)))
        window_key = f"{bucket_key}:fw:{int(time.time()) // window_s}"

        try:
            current = await redis.incr(window_key)
            await redis.expire(window_key, window_s)

            if current <= capacity:
                return RateLimitResult(
                    allowed=True,
                    remaining=capacity - current,
                    limit=capacity,
                    reset_seconds=window_s,
                )
            return RateLimitResult(
                allowed=False,
                retry_after_seconds=window_s,
                remaining=0,
                limit=capacity,
                reset_seconds=window_s,
            )
        except Exception as e:
            logger.error("In-memory rate limiter failure; allowing request: %s", e)
            return RateLimitResult(allowed=True, remaining=capacity, limit=capacity)

    async def _check_redis(
        self,
        redis: Any,
        bucket_key: str,
        now_ms: int,
        cost: int,
        capacity: int,
        rate_per_ms: float,
        refill_per_second: int,
    ) -> RateLimitResult:
        """Token bucket rate limiting for Redis backend."""
        try:
            res = await redis.eval(TOKEN_BUCKET_LUA, 1, bucket_key, now_ms, capacity, rate_per_ms, cost)
            allowed_int, tokens_left, retry_ms, cap = int(res[0]), int(res[1]), int(res[2]), int(res[3])
            allowed = allowed_int == 1

            # Calculate reset time (time until bucket is full again)
            tokens_needed = capacity - tokens_left
            reset_ms = int(tokens_needed / rate_per_ms) if rate_per_ms > 0 else 60000

            return RateLimitResult(
                allowed=allowed,
                retry_after_seconds=max(1, int((retry_ms + 999) // 1000)) if not allowed else 0,
                remaining=max(0, tokens_left),
                limit=capacity,
                reset_seconds=max(1, int((reset_ms + 999) // 1000)),
            )
        except Exception as e:
            logger.warning("Rate limiter Lua failed (%s); falling back to fixed-window", e)
            return await self._check_in_memory(
                redis, bucket_key, cost, capacity, refill_per_second
            )


class RateLimitService:
    """High-level rate limiting service for application-wide rate limiting.

    Provides specialized rate limiters for different use cases:
    - API rate limiting (general request limits)
    - Auth rate limiting (stricter limits for auth endpoints)
    - LLM rate limiting (RPM and TPM for LLM calls)

    Uses dependency injection for settings, follows SOLID principles.
    """

    def __init__(self, settings: Optional[Any] = None):
        """Initialize rate limit service.

        Args:
            settings: Application settings (uses get_settings_instance if not provided)
        """
        if settings is None:
            from .config import get_settings_instance
            settings = get_settings_instance()

        self._settings = settings
        self._enabled = getattr(settings, "enable_rate_limiting", True)

        # Initialize limiters lazily
        self._api_limiter: Optional[TokenBucketRateLimiter] = None
        self._auth_limiter: Optional[TokenBucketRateLimiter] = None
        # LLM limiters are per-provider, created on demand
        self._llm_rpm_limiter: Optional[TokenBucketRateLimiter] = None
        self._llm_tpm_limiter: Optional[TokenBucketRateLimiter] = None

    @property
    def enabled(self) -> bool:
        """Check if rate limiting is enabled."""
        return self._enabled

    def _get_api_limiter(self) -> TokenBucketRateLimiter:
        """Get or create API rate limiter."""
        if self._api_limiter is None:
            requests = getattr(self._settings, "rate_limit_requests", 100)
            period = getattr(self._settings, "rate_limit_period", 60)
            self._api_limiter = TokenBucketRateLimiter(
                namespace="rl:api",
                capacity=requests,
                refill_per_second=max(1, requests // period),
            )
        return self._api_limiter

    def _get_auth_limiter(self) -> TokenBucketRateLimiter:
        """Get or create auth rate limiter (stricter limits)."""
        if self._auth_limiter is None:
            # Use strict limits for auth endpoints
            requests = getattr(self._settings, "strict_rate_limit_requests", 10)
            self._auth_limiter = TokenBucketRateLimiter(
                namespace="rl:auth",
                capacity=requests,
                refill_per_second=1,  # Slow refill for auth
            )
        return self._auth_limiter

    def _get_llm_rpm_limiter(self, rpm_limit: int) -> TokenBucketRateLimiter:
        """Get or create LLM RPM rate limiter.

        Args:
            rpm_limit: Provider-specific RPM limit
        """
        # Create limiter with provider-specific capacity
        # Note: We reuse the cached limiter but override capacity per-call
        if self._llm_rpm_limiter is None:
            self._llm_rpm_limiter = TokenBucketRateLimiter(
                namespace="rl:llm:rpm",
                capacity=rpm_limit,
                refill_per_second=max(1, rpm_limit // 60),
            )
        return self._llm_rpm_limiter

    def _get_llm_tpm_limiter(self, tpm_limit: int) -> TokenBucketRateLimiter:
        """Get or create LLM TPM rate limiter.

        Args:
            tpm_limit: Provider-specific TPM limit
        """
        # Create limiter with provider-specific capacity
        # Note: We reuse the cached limiter but override capacity per-call
        if self._llm_tpm_limiter is None:
            self._llm_tpm_limiter = TokenBucketRateLimiter(
                namespace="rl:llm:tpm",
                capacity=tpm_limit,
                refill_per_second=max(1, tpm_limit // 60),
            )
        return self._llm_tpm_limiter

    async def check_api_limit(self, user_id: str) -> RateLimitResult:
        """Check API rate limit for a user.

        Args:
            user_id: User identifier

        Returns:
            RateLimitResult
        """
        if not self._enabled:
            return RateLimitResult(allowed=True, remaining=999, limit=999)

        limiter = self._get_api_limiter()
        return await limiter.check(key=f"user:{user_id}")

    async def check_auth_limit(self, identifier: str) -> RateLimitResult:
        """Check auth rate limit for an identifier (user email or IP).

        Uses stricter limits for brute-force protection.

        Args:
            identifier: Email or IP address

        Returns:
            RateLimitResult
        """
        if not self._enabled:
            return RateLimitResult(allowed=True, remaining=999, limit=999)

        limiter = self._get_auth_limiter()
        return await limiter.check(key=f"auth:{identifier}")

    async def check_llm_rpm_limit(
        self,
        user_id: str,
        provider_id: str,
        rpm_override: int,
    ) -> RateLimitResult:
        """Check LLM requests per minute limit for a specific provider.

        Args:
            user_id: User identifier
            provider_id: Provider identifier (required - no global limits)
            rpm_override: RPM limit from LLMProvider.rate_limit_rpm

        Returns:
            RateLimitResult
        """
        if not self._enabled:
            return RateLimitResult(allowed=True, remaining=999, limit=999)

        limiter = self._get_llm_rpm_limiter(rpm_override)
        key = f"user:{user_id}:provider:{provider_id}"
        refill = max(1, rpm_override // 60)

        return await limiter.check(key=key, capacity=rpm_override, refill_per_second=refill)

    async def check_llm_tpm_limit(
        self,
        user_id: str,
        token_cost: int,
        provider_id: str,
        tpm_override: int,
    ) -> RateLimitResult:
        """Check LLM tokens per minute limit for a specific provider.

        Args:
            user_id: User identifier
            token_cost: Estimated tokens for this request
            provider_id: Provider identifier (required - no global limits)
            tpm_override: TPM limit from LLMProvider.rate_limit_tpm

        Returns:
            RateLimitResult
        """
        if not self._enabled:
            return RateLimitResult(allowed=True, remaining=999, limit=999)

        limiter = self._get_llm_tpm_limiter(tpm_override)
        key = f"user:{user_id}:provider:{provider_id}"
        refill = max(1, tpm_override // 60)

        return await limiter.check(
            key=key, cost=token_cost, capacity=tpm_override, refill_per_second=refill
        )


# Module-level singleton for convenience (use dependency injection when possible)
_rate_limit_service: Optional[RateLimitService] = None


def get_rate_limit_service() -> RateLimitService:
    """Get the rate limit service singleton.

    Prefer dependency injection over this function when possible.
    """
    global _rate_limit_service
    if _rate_limit_service is None:
        _rate_limit_service = RateLimitService()
    return _rate_limit_service


async def get_rate_limit_service_dependency() -> RateLimitService:
    """FastAPI dependency for rate limit service."""
    return get_rate_limit_service()

