"""
Redis-backed token bucket limiter for Tools v1 (async).
- Uses get_redis_client() which returns an async Redis client or in-memory fallback.
- If in-memory client is detected, enforce best-effort per-process limits.
"""
from __future__ import annotations
import logging
import time
from typing import Optional, Any

from ..core.database import get_redis_client

logger = logging.getLogger(__name__)

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
return {allowed, math.ceil(tokens), retry_ms}
"""


class TokenBucketLimiter:
    def __init__(self, *, namespace: str = "rl:plugin", capacity: int, refill_per_second: int):
        self.namespace = namespace
        self.capacity = max(1, int(capacity))
        self.refill_per_second = max(1, int(refill_per_second))
        self._redis: Optional[Any] = None

    async def _get_redis(self):
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    def _key(self, *, bucket: str) -> str:
        return f"{self.namespace}:{bucket}"

    @staticmethod
    def _is_in_memory(redis_client: Any) -> bool:
        clsname = redis_client.__class__.__name__
        return "InMemory" in clsname or "Fake" in clsname

    async def allow(self, *, bucket: str, cost: int = 1, capacity: Optional[int] = None, refill_per_second: Optional[int] = None) -> tuple[bool, int]:
        """Attempt to consume 'cost' tokens.
        Returns (allowed, retry_after_seconds).
        Allows per-call overrides for capacity and refill rate.
        """
        redis = await self._get_redis()
        now_ms = int(time.time() * 1000)
        key = self._key(bucket=bucket)
        cap = max(1, int(capacity if capacity is not None else self.capacity))
        rps = max(1, int(refill_per_second if refill_per_second is not None else self.refill_per_second))
        rate_per_ms = float(rps) / 1000.0

        # In-memory path: naive fixed-window using INCR/EXPIRE
        if self._is_in_memory(redis):
            window_s = max(1, int(cap / max(1, rps)))
            window_key = f"{key}:fw:{int(time.time())//window_s}"
            try:
                n = await redis.incr(window_key)
                await redis.expire(window_key, window_s)
                if n <= cap:
                    return True, 0
                return False, window_s
            except Exception as e2:  # noqa: BLE001
                logger.error("In-memory rate limiter failure; allowing request. err=%s", e2)
                return True, 0

        # Real Redis: use Lua script for atomic token bucket
        try:
            res = await redis.eval(TOKEN_BUCKET_LUA, 1, key, now_ms, cap, rate_per_ms, cost)
            allowed, _tokens_left, retry_ms = int(res[0]), int(res[1]), int(res[2])  # type: ignore[index]
            if allowed == 1:
                return True, 0
            return False, max(1, int((retry_ms + 999) // 1000))
        except Exception as e:
            logger.warning("Rate limiter Lua path failed (%s); falling back to fixed-window.", e)
            window_s = max(1, int(cap / max(1, rps)))
            window_key = f"{key}:fw:{int(time.time())//window_s}"
            try:
                n = await redis.incr(window_key)
                await redis.expire(window_key, window_s)
                if n <= cap:
                    return True, 0
                return False, window_s
            except Exception as e2:
                logger.error("Rate limiter failure; allowing request. err=%s", e2)
                return True, 0
