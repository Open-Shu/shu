"""Tenant-isolation helpers shared across the cache and queue backends.

Both Redis-backed factories need to warn when SHU_TENANT_ID is configured
without SHU_REDIS_URL — that combination would silently land tenant state
in a per-pod in-memory backend other pods cannot see. The message shape
must stay in lockstep across factories; centralising it here prevents
drift.
"""

from logging import Logger


def warn_tenant_without_redis(logger: Logger, backend_kind: str, tenant_id: str) -> None:
    logger.warning(
        "SHU_TENANT_ID is set (tenant=%s) but SHU_REDIS_URL is not — " "falling back to in-memory %s backend.",
        tenant_id,
        backend_kind,
    )
