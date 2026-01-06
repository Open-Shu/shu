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
    """
    Return the client's IP address derived from request headers or a provided host fallback.
    
    Parameters:
        headers (Dict[str, str]): Request headers or a dict-like object; if the `X-Forwarded-For` header is present the first IP in the comma-separated list is used.
        client_host (Optional[str]): Direct client host to use when `X-Forwarded-For` is not present.
    
    Returns:
        str: The chosen client IP address, or "unknown" if neither header nor `client_host` is available.
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
        """
        Builds HTTP rate limit headers representing the current rate limit state.
        
        Includes `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset`; adds `Retry-After` when the request was denied.
        
        Returns:
            headers (dict[str, str]): Mapping of HTTP header names to string values. Contains `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset`; contains `Retry-After` if `allowed` is `False`.
        """
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
        """
        Determine whether a request identified by `key` may proceed and consume the appropriate quota.
        
        Parameters:
            key (str): Unique identifier for the rate limit bucket (for example, "user:<id>" or "auth:<identifier>").
            cost (int): Number of tokens to consume for this operation (use 1 for a single request or the token cost for TPM scenarios).
            capacity (Optional[int]): Optional override for the bucket capacity (maximum tokens).
            refill_per_second (Optional[int]): Optional override for the refill rate in tokens per second.
        
        Returns:
            RateLimitResult: Result describing whether the request is allowed and related metadata (`allowed`, `retry_after_seconds`, `remaining`, `limit`, `reset_seconds`).
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
-- TTL of 1 hour (3600000ms) for cleanup of abandoned buckets
-- The bucket state must persist across the entire rate limit window and beyond
-- to prevent users from bypassing limits by waiting for key expiration
redis.call('PEXPIRE', key, 3600000)
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
        refill_per_second: float = 1.0,
    ):
        """Initialize rate limiter.

        Args:
            namespace: Redis key namespace (e.g., "rl:api", "rl:auth")
            capacity: Maximum tokens in bucket (burst capacity)
            refill_per_second: Tokens added per second (sustained rate, can be fractional)
        """
        self.namespace = namespace
        self.capacity = max(1, int(capacity))
        # Allow fractional refill rates for per-minute limits (e.g., 2 RPM = 0.0333 tokens/sec)
        self.refill_per_second = max(0.001, float(refill_per_second))
        self._redis: Optional[Any] = None
    
    async def _get_redis(self) -> Any:
        """
        Lazily obtain and cache the Redis client used by this limiter.
        
        Calls get_redis_client() on first access and stores the resulting client for reuse on subsequent calls.
        
        Returns:
            redis_client (Any): The cached Redis client instance.
        """
        if self._redis is None:
            from .database import get_redis_client
            self._redis = await get_redis_client()
        return self._redis
    
    def _key(self, bucket: str) -> str:
        """
        Build a namespaced Redis key for the given limiter bucket.
        
        Parameters:
            bucket (str): Bucket identifier appended to the rate limiter namespace.
        
        Returns:
            str: Redis key in the form "<namespace>:<bucket>".
        """
        return f"{self.namespace}:{bucket}"
    
    @staticmethod
    def _is_in_memory(redis_client: Any) -> bool:
        """
        Detects whether the provided Redis client is an in-memory or fake implementation.
        
        Returns:
            True if the client's class name contains "InMemory" or "Fake", False otherwise.
        """
        clsname = redis_client.__class__.__name__
        return "InMemory" in clsname or "Fake" in clsname

    async def check(
        self,
        key: str,
        cost: int = 1,
        capacity: Optional[int] = None,
        refill_per_second: Optional[float] = None,
    ) -> RateLimitResult:
        """
        Determine whether a request is allowed and consume the requested tokens from the corresponding rate limit bucket.
        
        Parameters:
            key (str): Unique identifier for the rate limit bucket.
            cost (int): Number of tokens to consume from the bucket.
            capacity (Optional[int]): Optional override for the bucket capacity.
            refill_per_second (Optional[float]): Optional override for the refill rate; may be fractional.
        
        Returns:
            RateLimitResult: Result containing `allowed` and rate-limit metadata (`remaining`, `limit`, `reset_seconds`, `retry_after_seconds`).
        """
        redis = await self._get_redis()
        now_ms = int(time.time() * 1000)
        bucket_key = self._key(key)
        cap = max(1, int(capacity if capacity is not None else self.capacity))
        # Support fractional refill rates for per-minute limits
        rps = max(0.001, float(refill_per_second if refill_per_second is not None else self.refill_per_second))
        rate_per_ms = rps / 1000.0

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
        refill_per_second: float,
    ) -> RateLimitResult:
        """
        Fixed-window fallback rate limiter used when Redis scripting is unavailable.
        
        Calculates a window size from capacity and refill_per_second (minimum 60 seconds), increments a counter for the current window by `cost`, and grants or denies the request based on whether the windowed count exceeds `capacity`.
        
        Parameters:
            redis (Any): Redis-like client providing `incrby` and `expire`.
            bucket_key (str): Base key identifying the rate-limited bucket.
            cost (int): Number of tokens to consume for this request (1 for RPM, >1 for token-costing TPM).
            capacity (int): Maximum tokens allowed per window.
            refill_per_second (float): Token refill rate per second used to derive the window duration.
        
        Returns:
            RateLimitResult: Result containing `allowed`, `remaining`, `limit`, `reset_seconds`, and `retry_after_seconds` when denied.
        """
        # For per-minute limits, window is capacity/refill_rate
        # E.g., 2 RPM: capacity=2, refill=0.0333, window = 2/0.0333 = 60s
        window_s = max(60, int(capacity / max(0.001, refill_per_second)))
        window_key = f"{bucket_key}:fw:{int(time.time()) // window_s}"

        logger.debug(
            "In-memory rate limit check: key=%s, cost=%d, capacity=%d, window_s=%d",
            window_key, cost, capacity, window_s
        )

        try:
            # Use incrby to properly handle cost (token count for TPM, 1 for RPM)
            current = await redis.incrby(window_key, cost)
            await redis.expire(window_key, window_s)

            logger.debug(
                "In-memory rate limit result: key=%s, current=%d, capacity=%d, allowed=%s",
                window_key, current, capacity, current <= capacity
            )

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
        refill_per_second: float,
    ) -> RateLimitResult:
        """
        Perform a token-bucket check using the Redis backend and return the resulting rate limit metadata.
        
        Calls a Redis Lua script to attempt consuming `cost` tokens from the bucket identified by `bucket_key` using `rate_per_ms` as the refill rate. If the Redis call fails, falls back to the in-memory fixed-window check and returns its result.
        
        Parameters:
            redis: Redis client used to execute the Lua script.
            bucket_key (str): Key identifying the token bucket in Redis.
            now_ms (int): Current time in milliseconds passed to the script.
            cost (int): Number of tokens to consume for this request.
            capacity (int): Maximum number of tokens the bucket can hold.
            rate_per_ms (float): Refill rate expressed in tokens per millisecond.
            refill_per_second (float): Refill rate expressed in tokens per second; used by the in-memory fallback.
        
        Returns:
            RateLimitResult: Result describing whether the request is allowed, remaining tokens, total limit, retry-after seconds when denied, and seconds until the bucket is fully reset.
        """
        try:
            res = await redis.eval(TOKEN_BUCKET_LUA, 1, bucket_key, now_ms, capacity, rate_per_ms, cost)
            allowed_int, tokens_left, retry_ms, _ = int(res[0]), int(res[1]), int(res[2]), int(res[3])
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
        """
        Whether rate limiting is enabled according to the service settings.
        
        Returns:
            `true` if rate limiting is enabled, `false` otherwise.
        """
        return self._enabled

    def _get_api_limiter(self) -> TokenBucketRateLimiter:
        """
        Get the TokenBucketRateLimiter used for API rate limiting, creating and configuring it from settings if not already initialized.
        
        Returns:
            TokenBucketRateLimiter: Limiter configured for the "rl:api" namespace. Capacity is taken from `settings.rate_limit_requests` (default 100) and `refill_per_second` is computed as capacity divided by `settings.rate_limit_period` (default 60).
        """
        if self._api_limiter is None:
            requests = getattr(self._settings, "rate_limit_requests", 100)
            period = getattr(self._settings, "rate_limit_period", 60)
            self._api_limiter = TokenBucketRateLimiter(
                namespace="rl:api",
                capacity=requests,
                # Fractional refill: requests per second = requests / period
                refill_per_second=requests / float(period),
            )
        return self._api_limiter

    def _get_auth_limiter(self) -> TokenBucketRateLimiter:
        """
        Get or create a TokenBucketRateLimiter configured for authentication with strict, slow-refill limits.
        
        Reads `strict_rate_limit_requests` from settings (default 10) for capacity and sets `refill_per_second` to 1.
        
        Returns:
            TokenBucketRateLimiter: Limiter instance used for auth rate limiting.
        """
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
        """
        Get a TokenBucketRateLimiter configured for LLM RPM using the provided provider-specific capacity.
        
        Parameters:
            rpm_limit (int): Provider-specific requests-per-minute capacity used to initialize the limiter.
        
        Returns:
            TokenBucketRateLimiter: A limiter namespaced for LLM RPM with capacity set to `rpm_limit` and refill rate of `rpm_limit / 60`.
        """
        # Create limiter with provider-specific capacity
        # Note: We reuse the cached limiter but override capacity per-call
        if self._llm_rpm_limiter is None:
            self._llm_rpm_limiter = TokenBucketRateLimiter(
                namespace="rl:llm:rpm",
                capacity=rpm_limit,
                # Fractional refill: e.g., 2 RPM = 2/60 = 0.0333 tokens/sec
                refill_per_second=rpm_limit / 60.0,
            )
        return self._llm_rpm_limiter

    def _get_llm_tpm_limiter(self, tpm_limit: int) -> TokenBucketRateLimiter:
        """
        Return a TokenBucketRateLimiter configured for provider-specific tokens-per-minute (TPM) limits.
        
        Parameters:
            tpm_limit (int): Provider-specific TPM capacity to use when creating the limiter.
        
        Returns:
            TokenBucketRateLimiter: A limiter scoped to "rl:llm:tpm". The limiter is created once and cached for reuse.
        """
        # Create limiter with provider-specific capacity
        # Note: We reuse the cached limiter but override capacity per-call
        if self._llm_tpm_limiter is None:
            self._llm_tpm_limiter = TokenBucketRateLimiter(
                namespace="rl:llm:tpm",
                capacity=tpm_limit,
                # Fractional refill: tokens per second = TPM / 60
                refill_per_second=tpm_limit / 60.0,
            )
        return self._llm_tpm_limiter

    async def check_api_limit(self, user_id: str) -> RateLimitResult:
        """
        Enforces the configured API rate limit for the given user.
        
        Returns:
            RateLimitResult containing whether the request is allowed. When denied, `retry_after_seconds` indicates how long to wait; the result also includes `remaining`, `limit`, and `reset_seconds` metadata.
        """
        if not self._enabled:
            return RateLimitResult(allowed=True, remaining=999, limit=999)

        limiter = self._get_api_limiter()
        return await limiter.check(key=f"user:{user_id}")

    async def check_auth_limit(self, identifier: str) -> RateLimitResult:
        """
        Enforce a stricter authentication rate limit for the given identifier.
        
        Used for brute-force protection: when disabled this returns an allowed result with large remaining/limit; otherwise the configured auth limiter is applied.
        
        Parameters:
            identifier (str): Email address or IP address that identifies the actor being rate-limited.
        
        Returns:
            RateLimitResult: Outcome of the rate limit check, including `allowed`, `retry_after_seconds`, `remaining`, `limit`, and `reset_seconds`.
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
        """
        Enforces the per-provider LLM requests-per-minute limit for a given user.
        
        Parameters:
            user_id: User identifier.
            provider_id: Provider identifier—limits are applied per provider (no global provider-level limits).
            rpm_override: RPM limit from the provider configuration to use for this check.
        
        Returns:
            A RateLimitResult describing whether the request is allowed and containing remaining, limit, retry-after, and reset information.
        """
        if not self._enabled:
            return RateLimitResult(allowed=True, remaining=999, limit=999)

        limiter = self._get_llm_rpm_limiter(rpm_override)
        key = f"user:{user_id}:provider:{provider_id}"
        # Fractional refill: e.g., 2 RPM = 2/60 = 0.0333 tokens/sec
        refill = rpm_override / 60.0

        return await limiter.check(key=key, capacity=rpm_override, refill_per_second=refill)

    async def check_llm_tpm_limit(
        self,
        user_id: str,
        token_cost: int,
        provider_id: str,
        tpm_override: int,
    ) -> RateLimitResult:
        """
        Check whether the user may consume the given token cost against the provider-specific tokens-per-minute (TPM) quota.
        
        Parameters:
            user_id (str): User identifier.
            token_cost (int): Estimated token cost of this request; subtracted from the bucket when allowed.
            provider_id (str): Provider identifier; limits are applied per provider and no global provider fallback is used.
            tpm_override (int): TPM capacity to enforce for this check (tokens per minute).
        
        Returns:
            RateLimitResult: Result describing whether the request is allowed, remaining tokens, limit, retry-after (if denied), and reset time.
        """
        if not self._enabled:
            return RateLimitResult(allowed=True, remaining=999, limit=999)

        limiter = self._get_llm_tpm_limiter(tpm_override)
        key = f"user:{user_id}:provider:{provider_id}"
        # Fractional refill: tokens per second = TPM / 60
        refill = tpm_override / 60.0

        return await limiter.check(
            key=key, cost=token_cost, capacity=tpm_override, refill_per_second=refill
        )


# Module-level singleton for convenience (use dependency injection when possible)
_rate_limit_service: Optional[RateLimitService] = None


def get_rate_limit_service() -> RateLimitService:
    """
    Retrieve the module-level RateLimitService singleton.
    
    Creates and stores the singleton on first invocation. Prefer injecting a RateLimitService via dependency injection when possible.
    
    Returns:
        RateLimitService: The shared RateLimitService instance used by the application.
    """
    global _rate_limit_service
    if _rate_limit_service is None:
        _rate_limit_service = RateLimitService()
    return _rate_limit_service


async def get_rate_limit_service_dependency() -> RateLimitService:
    """
    Provide the module's RateLimitService as a FastAPI dependency.
    
    Returns:
        RateLimitService: The singleton RateLimitService instance.
    """
    return get_rate_limit_service()
