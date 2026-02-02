"""Rate limiting service for Shu.

Provides a unified rate limiting interface using the CacheBackend abstraction.
Supports both RPM (requests per minute) and TPM (tokens per minute) limiting
with a fixed-window algorithm that works identically across all cache backends.

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
from typing import Protocol

from .cache_backend import CacheBackend, get_cache_backend

logger = logging.getLogger(__name__)


def get_client_ip(headers: dict[str, str], client_host: str | None = None) -> str:
    """Return the client's IP address derived from request headers or a provided host fallback.

    Parameters
    ----------
        headers (Dict[str, str]): Request headers or a dict-like object; if the `X-Forwarded-For` header is present the first IP in the comma-separated list is used.
        client_host (Optional[str]): Direct client host to use when `X-Forwarded-For` is not present.

    Returns
    -------
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
        """Build HTTP rate limit headers representing the current rate limit state.

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
        capacity: int | None = None,
        refill_per_second: float | None = None,
    ) -> RateLimitResult:
        """Determine whether a request identified by `key` may proceed and consume the appropriate quota.

        Parameters
        ----------
            key (str): Unique identifier for the rate limit bucket (for example, "user:<id>" or "auth:<identifier>").
            cost (int): Number of tokens to consume for this operation (use 1 for a single request or the token cost for TPM scenarios).
            capacity (Optional[int]): Optional override for the bucket capacity (maximum tokens).
            refill_per_second (Optional[float]): Optional override for the refill rate in tokens per second; may be fractional.

        Returns
        -------
            RateLimitResult: Result describing whether the request is allowed and related metadata (`allowed`, `retry_after_seconds`, `remaining`, `limit`, `reset_seconds`).

        """
        ...


class TokenBucketRateLimiter:
    """Fixed-window rate limiter using CacheBackend.

    Uses a fixed-window algorithm that works identically across all cache backends.
    This replaces the previous token bucket + Lua script approach for consistency.
    """

    def __init__(
        self,
        namespace: str = "rl",
        capacity: int = 60,
        refill_per_second: float = 1.0,
    ) -> None:
        """Initialize rate limiter.

        Args:
            namespace: Cache key namespace (e.g., "rl:api", "rl:auth")
            capacity: Maximum tokens in window (burst capacity)
            refill_per_second: Tokens added per second (sustained rate, can be fractional)

        """
        self.namespace = namespace
        self.capacity = max(1, int(capacity))
        # Allow fractional refill rates for per-minute limits (e.g., 2 RPM = 0.0333 tokens/sec)
        self.refill_per_second = max(0.001, float(refill_per_second))
        self._cache: CacheBackend | None = None

    async def _get_cache(self) -> CacheBackend:
        """Lazily obtain and cache the CacheBackend used by this limiter.

        Returns:
            CacheBackend: The cached backend instance.

        """
        if self._cache is None:
            self._cache = await get_cache_backend()
        return self._cache

    def _key(self, bucket: str) -> str:
        """Build a namespaced cache key for the given limiter bucket.

        Parameters
        ----------
            bucket (str): Bucket identifier appended to the rate limiter namespace.

        Returns
        -------
            str: Cache key in the form "<namespace>:<bucket>".

        """
        return f"{self.namespace}:{bucket}"

    async def check(
        self,
        key: str,
        cost: int = 1,
        capacity: int | None = None,
        refill_per_second: float | None = None,
    ) -> RateLimitResult:
        """Determine whether a request is allowed using fixed-window algorithm.

        Parameters
        ----------
            key (str): Unique identifier for the rate limit bucket.
            cost (int): Number of tokens to consume from the bucket.
            capacity (Optional[int]): Optional override for the bucket capacity.
            refill_per_second (Optional[float]): Optional override for the refill rate; may be fractional.

        Returns
        -------
            RateLimitResult: Result containing `allowed` and rate-limit metadata.

        """
        cache = await self._get_cache()
        bucket_key = self._key(key)
        cap = max(1, int(capacity if capacity is not None else self.capacity))
        # Support fractional refill rates for per-minute limits
        rps = max(
            0.001,
            float(refill_per_second if refill_per_second is not None else self.refill_per_second),
        )

        # Fixed-window algorithm: calculate window size and current window key
        # For per-minute limits, window is capacity/refill_rate
        # E.g., 2 RPM: capacity=2, refill=0.0333, window = 2/0.0333 = 60s
        #
        # Design note: 60-second minimum window is enforced for operational stability.
        # This ensures rate limit windows align with typical per-minute configurations
        # and prevents excessive cache key churn from very short windows. Standard
        # configurations (e.g., 100 requests/minute with refill=100/60) naturally
        # produce ~60s windows. Shorter windows would create more cache entries and
        # increase backend load without meaningful benefit for typical API rate limiting.
        # If sub-minute windows are needed, this minimum can be made configurable.
        window_s = max(60, int(cap / rps))
        window_key = f"{bucket_key}:fw:{int(time.time()) // window_s}"

        logger.debug(
            "Fixed-window rate limit check: key=%s, cost=%d, capacity=%d, window_s=%d",
            window_key,
            cost,
            cap,
            window_s,
        )

        try:
            # Atomic increment-first pattern to avoid TOCTOU race condition
            # Increment first, then check if we exceeded capacity
            new_count = await cache.incr(window_key, cost)

            # Set expiry only when key was just created (new_count == cost means first increment)
            if new_count == cost:
                await cache.expire(window_key, window_s)

            logger.debug(
                "Fixed-window rate limit check: key=%s, new_count=%d, capacity=%d",
                window_key,
                new_count,
                cap,
            )

            # Check if within capacity after increment
            if new_count <= cap:
                return RateLimitResult(
                    allowed=True,
                    remaining=cap - new_count,
                    limit=cap,
                    reset_seconds=window_s,
                )

            # Over capacity - decrement back and deny
            try:
                await cache.decr(window_key, cost)
            except Exception as decr_err:
                logger.error(
                    "Failed to decrement rate limit counter after exceeding capacity: key=%s, cost=%d, err=%s",
                    window_key,
                    cost,
                    decr_err,
                )
            return RateLimitResult(
                allowed=False,
                retry_after_seconds=window_s,
                remaining=0,
                limit=cap,
                reset_seconds=window_s,
            )
        except Exception:
            logger.exception("Rate limiter failure; allowing request")
            return RateLimitResult(allowed=True, remaining=cap, limit=cap)


class RateLimitService:
    """High-level rate limiting service for application-wide rate limiting.

    Provides specialized rate limiters for different use cases:
    - API rate limiting (general request limits)
    - Auth rate limiting (stricter limits for auth endpoints)
    - LLM rate limiting (RPM and TPM for LLM calls)

    Uses dependency injection for settings, follows SOLID principles.
    All rate limiting uses fixed-window algorithm via CacheBackend.
    """

    def __init__(self, settings: Any | None = None) -> None:
        """Initialize rate limit service.

        Args:
            settings: Application settings (uses get_settings_instance if not provided)

        """
        if settings is None:
            from .config import get_settings_instance

            settings = get_settings_instance()

        self._settings = settings
        self._enabled = getattr(settings, "enable_api_rate_limiting", False)

        # Initialize limiters lazily
        self._api_limiter: TokenBucketRateLimiter | None = None
        self._auth_limiter: TokenBucketRateLimiter | None = None
        # LLM limiters are per-provider, created on demand
        self._llm_rpm_limiter: TokenBucketRateLimiter | None = None
        self._llm_tpm_limiter: TokenBucketRateLimiter | None = None

    @property
    def enabled(self) -> bool:
        """Whether rate limiting is enabled according to the service settings.

        Returns:
            `true` if rate limiting is enabled, `false` otherwise.

        """
        return self._enabled

    def _get_api_limiter(self) -> TokenBucketRateLimiter:
        """Get the TokenBucketRateLimiter used for API rate limiting, creating and configuring it from settings if not already initialized.

        Returns:
            TokenBucketRateLimiter: Limiter configured for the "rl:api" namespace. Capacity is taken from `settings.api_rate_limit_requests` (default 100) and `refill_per_second` is computed as capacity divided by `settings.api_rate_limit_period` (default 60).

        """
        if self._api_limiter is None:
            requests = getattr(self._settings, "api_rate_limit_requests", 100)
            period = getattr(self._settings, "api_rate_limit_period", 60)
            self._api_limiter = TokenBucketRateLimiter(
                namespace="rl:api",
                capacity=requests,
                # Fractional refill: requests per second = requests / period
                refill_per_second=requests / float(period),
            )
        return self._api_limiter

    def _get_auth_limiter(self) -> TokenBucketRateLimiter:
        """Get or create a TokenBucketRateLimiter configured for authentication with strict, slow-refill limits.

        Reads `strict_api_rate_limit_requests` from settings (default 10) for capacity and sets `refill_per_second` to 1.

        Returns:
            TokenBucketRateLimiter: Limiter instance used for auth rate limiting.

        """
        if self._auth_limiter is None:
            # Use strict limits for auth endpoints
            requests = getattr(self._settings, "strict_api_rate_limit_requests", 10)
            self._auth_limiter = TokenBucketRateLimiter(
                namespace="rl:auth",
                capacity=requests,
                refill_per_second=1,  # Slow refill for auth
            )
        return self._auth_limiter

    def _get_llm_rpm_limiter(self, rpm_limit: int) -> TokenBucketRateLimiter:
        """Get a TokenBucketRateLimiter configured for LLM RPM using the provided provider-specific capacity.

        Parameters
        ----------
            rpm_limit (int): Provider-specific requests-per-minute capacity used to initialize the limiter.

        Returns
        -------
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
        """Return a TokenBucketRateLimiter configured for provider-specific tokens-per-minute (TPM) limits.

        Parameters
        ----------
            tpm_limit (int): Provider-specific TPM capacity to use when creating the limiter.

        Returns
        -------
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
        """Enforces the configured API rate limit for the given user.

        Returns:
            RateLimitResult containing whether the request is allowed. When denied, `retry_after_seconds` indicates how long to wait; the result also includes `remaining`, `limit`, and `reset_seconds` metadata.

        """
        if not self._enabled:
            return RateLimitResult(allowed=True, remaining=999, limit=999)

        limiter = self._get_api_limiter()
        return await limiter.check(key=f"user:{user_id}")

    async def check_auth_limit(self, identifier: str) -> RateLimitResult:
        """Enforce a stricter authentication rate limit for the given identifier.

        Used for brute-force protection: when disabled this returns an allowed result with large remaining/limit; otherwise the configured auth limiter is applied.

        Parameters
        ----------
            identifier (str): Email address or IP address that identifies the actor being rate-limited.

        Returns
        -------
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
        """Enforces the per-provider LLM requests-per-minute limit for a given user.

        Parameters
        ----------
            user_id: User identifier.
            provider_id: Provider identifier—limits are applied per provider (no global provider-level limits).
            rpm_override: RPM limit from the provider configuration to use for this check.

        Returns
        -------
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
        """Check whether the user may consume the given token cost against the provider-specific tokens-per-minute (TPM) quota.

        Parameters
        ----------
            user_id (str): User identifier.
            token_cost (int): Estimated token cost of this request; subtracted from the bucket when allowed.
            provider_id (str): Provider identifier; limits are applied per provider and no global provider fallback is used.
            tpm_override (int): TPM capacity to enforce for this check (tokens per minute).

        Returns
        -------
            RateLimitResult: Result describing whether the request is allowed, remaining tokens, limit, retry-after (if denied), and reset time.

        """
        if not self._enabled:
            return RateLimitResult(allowed=True, remaining=999, limit=999)

        limiter = self._get_llm_tpm_limiter(tpm_override)
        key = f"user:{user_id}:provider:{provider_id}"
        # Fractional refill: tokens per second = TPM / 60
        refill = tpm_override / 60.0

        return await limiter.check(key=key, cost=token_cost, capacity=tpm_override, refill_per_second=refill)


# Module-level singleton for convenience (use dependency injection when possible)
_rate_limit_service: RateLimitService | None = None


def get_rate_limit_service() -> RateLimitService:
    """Retrieve the module-level RateLimitService singleton.

    Creates and stores the singleton on first invocation. Prefer injecting a RateLimitService via dependency injection when possible.

    Returns:
        RateLimitService: The shared RateLimitService instance used by the application.

    """
    global _rate_limit_service
    if _rate_limit_service is None:
        _rate_limit_service = RateLimitService()
    return _rate_limit_service


async def get_rate_limit_service_dependency() -> RateLimitService:
    """Provide the module's RateLimitService as a FastAPI dependency.

    Returns:
        RateLimitService: The singleton RateLimitService instance.

    """
    return get_rate_limit_service()
