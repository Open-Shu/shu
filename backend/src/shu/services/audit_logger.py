"""Audit-log emission for cross-tenant admin operations.

Cross-tenant work (impersonate, cross_tenant_query) must be observable: an
internal admin reading another tenant's data has to leave a trace that
includes who, when, what tenant, and why. ``AuditLogger`` is the seam that
keeps the policy (always log) decoupled from the transport (stdout structured
logs today, a SIEM stream or audit table tomorrow).

The default implementation writes through ``core.logging`` to stdout JSON,
which is the same path application logs already take. Crucially, it
**re-raises** any exception the underlying logger throws — the design treats
unauditable cross-tenant work as a hard stop, not a best-effort emission.
The ``TenantAdminService`` context managers call ``log`` before yielding the
session, so a failure here short-circuits the operation before any data is
touched.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from shu.core.logging import get_logger

_logger = get_logger(__name__)


@runtime_checkable
class AuditLogger(Protocol):
    """Protocol for emitting cross-tenant audit events.

    Implementations must raise on failure rather than swallowing — the caller
    (``TenantAdminService``) treats audit emission as a precondition for the
    operation, not a side effect.
    """

    async def log(
        self,
        *,
        event: str,
        actor: str,
        target: str | None = None,
        reason: str | None = None,
        **extra: Any,
    ) -> None:
        """Emit one audit record.

        Args:
            event: Stable event name (e.g. ``impersonate_tenant_open``).
                Used by downstream tooling for filtering, so callers should
                pick from a small enumerated set rather than freeforming.
            actor: User id of the internal admin performing the operation.
            target: Tenant id being impersonated (impersonate path) or None
                (cross-tenant-query path, since the operation spans tenants).
            reason: Free-text justification supplied by the actor.
            **extra: Additional context (request id, etc.) merged into the
                log record.

        """
        ...


class DefaultAuditLogger:
    """``AuditLogger`` implementation backed by ``core.logging``.

    Records flow to the same stdout JSON stream as application logs, tagged
    with ``audit=True`` so log shippers can route them onward without
    inspecting the message string.
    """

    async def log(
        self,
        *,
        event: str,
        actor: str,
        target: str | None = None,
        reason: str | None = None,
        **extra: Any,
    ) -> None:
        # `extra` on logger.info accepts arbitrary keys, which the JSON
        # formatter promotes to top-level fields in the emitted record.
        record_fields: dict[str, Any] = {
            "audit": True,
            "event": event,
            "actor": actor,
            "target": target,
            "reason": reason,
            **extra,
        }
        # Wrap the emit so transport failures (filesystem full, handler
        # raised, etc.) propagate. Stdlib's `logging` mostly silences
        # internal errors, but a misbehaving handler can still raise.
        try:
            _logger.info("audit", extra=record_fields)
        except Exception as exc:
            raise AuditLogEmitError("Failed to emit audit log record") from exc


class AuditLogEmitError(RuntimeError):
    """Raised when the audit logger cannot durably emit a record.

    Cross-tenant admin paths catch nothing — this propagates out of the
    ``TenantAdminService`` context managers so the calling endpoint returns
    503 and the operator addresses the audit infrastructure before any
    cross-tenant work proceeds.
    """
