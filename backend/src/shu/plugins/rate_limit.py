"""
Fixed-window rate limiter for Tools v1 (async).
- Uses CacheBackend which provides unified interface across Redis and in-memory backends.
- Uses fixed-window algorithm for consistent behavior across all backends.
"""
from __future__ import annotations
import logging
import time
from typing import Optional

from ..core.cache_backend import get_cache_backend, CacheBackend

logger = logging.getLogger(__name__)


class TokenBucketLimiter:
    """Fixed-window rate limiter using CacheBackend.
    
    Uses the same fixed-window algorithm as core rate limiting for consistency.
    """
    
    def __init__(self, *, namespace: str = "rl:plugin", capacity: int, refill_per_second: int):
        self.namespace = namespace
        self.capacity = max(1, int(capacity))
        self.refill_per_second = max(1, int(refill_per_second))
        self._cache: Optional[CacheBackend] = None

    async def _get_cache(self) -> CacheBackend:
        """Get the CacheBackend instance."""
        if self._cache is None:
            self._cache = await get_cache_backend()
        return self._cache

    def _key(self, *, bucket: str) -> str:
        return f"{self.namespace}:{bucket}"

    async def allow(self, *, bucket: str, cost: int = 1, capacity: Optional[int] = None, refill_per_second: Optional[int] = None) -> tuple[bool, int]:
        """Attempt to consume 'cost' tokens using fixed-window algorithm.
        Returns (allowed, retry_after_seconds).
        Allows per-call overrides for capacity and refill rate.
        """
        cache = await self._get_cache()
        key = self._key(bucket=bucket)
        cap = max(1, int(capacity if capacity is not None else self.capacity))
        rps = max(1, int(refill_per_second if refill_per_second is not None else self.refill_per_second))

        # Fixed-window algorithm matching core rate limiting
        # Design note: 60-second minimum window is enforced for operational stability,
        # matching the core rate_limiting.py implementation. See core module for rationale.
        window_s = max(60, int(cap / rps))
        window_key = f"{key}:fw:{int(time.time())//window_s}"
        
        try:
            # Atomic increment-first pattern to avoid TOCTOU race condition
            new_count = await cache.incr(window_key, cost)
            
            # Set expiry only when key was just created (new_count == cost means first increment)
            # This avoids racing and resetting TTLs on subsequent increments
            if new_count == cost:
                await cache.expire(window_key, window_s)
            
            if new_count <= cap:
                return True, 0
            
            # Over capacity - decrement back and deny
            try:
                await cache.decr(window_key, cost)
            except Exception as decr_err:
                logger.error(
                    "Failed to decrement rate limit counter after exceeding capacity: key=%s, bucket=%s, err=%s",
                    window_key, bucket, decr_err
                )
            return False, window_s
        except Exception as e:
            logger.error("Rate limiter failure; allowing request. err=%s", e)
            return True, 0
