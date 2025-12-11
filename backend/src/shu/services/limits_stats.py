"""
Limits/quotas stats snapshot helper using Redis SCAN (non-blocking).
"""
from __future__ import annotations
from typing import Any, List, Dict

from ..core.database import get_redis_client


async def get_limits_stats(prefix: str, limit: int) -> Dict[str, Any]:
    redis = await get_redis_client()
    keys: List[str] = []
    pattern = f"{prefix}*"

    # Always use SCAN to avoid blocking behaviors of KEYS in production
    cursor = 0
    keys = []
    iters = 0
    max_iters = 50  # defensive cap
    count = min(1000, max(10, limit * 2))
    while iters < max_iters:
        iters += 1
        cursor, batch = await redis.scan(cursor=cursor, match=pattern, count=count)
        keys.extend(batch)
        if cursor == 0 or len(keys) >= limit:
            break

    # Trim to requested limit
    keys = keys[: max(0, int(limit))]

    entries = []
    for k in keys:
        try:
            t = await redis.type(k)
        except Exception:
            t = "unknown"
        try:
            ttl = await redis.ttl(k)
        except Exception:
            ttl = -2
        value: Any = None
        if t == "hash":
            try:
                value = await redis.hgetall(k)
            except Exception:
                value = None
        else:
            try:
                value = await redis.get(k)
            except Exception:
                value = None
        entries.append({
            "key": k,
            "key_tail": k[len(prefix):] if k.startswith(prefix) else None,
            "data_type": t,
            "ttl_seconds": ttl if isinstance(ttl, int) and ttl >= 0 else None,
            "value": value,
        })

    return {"prefix": prefix, "entries": entries}

