"""Cross-tenant admin operations gated on audit emission.

Two operation shapes, each with its own role binding and isolation story:

* ``impersonate_tenant`` — open a ``shu_app`` session with ``tenant_context``
  set to the target. RLS still applies (shu_app does not have BYPASSRLS), so
  the operator sees exactly what that tenant's app code would see. Used by
  support reading another tenant's data to debug a ticket.

* ``cross_tenant_query`` — open a ``shu_admin`` session with no tenant
  context. ``shu_admin`` has BYPASSRLS, so RLS is bypassed entirely; callers
  must write explicit ``WHERE tenant_id = ...`` predicates to scope. Used by
  internal analytics (cost reports, usage summaries) that genuinely span
  tenants.

Both paths audit-log entry and exit. Emission happens **before** the session
yields — if the audit logger raises, the session never opens and the caller
sees the audit failure, not a successful operation with a missing record.
The exit audit fires in a ``finally`` so the trail never loses an event,
even when the caller's body raises; the exit event is ``*_close`` on a
clean exit and ``*_aborted`` (carrying the original error class) on a raise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shu.core.logging import get_logger
from shu.core.tenant import tenant_context_for_tenant_id
from shu.services.audit_logger import AuditLogger

logger = get_logger(__name__)


class TenantAdminService:
    """Owns the two cross-tenant admin patterns.

    Constructor takes session factories rather than engines so test code can
    inject in-memory sessionmakers without standing up a real Postgres
    connection. The default wire-up (see ``get_tenant_admin_service`` in the
    admin router module) reads ``get_async_session_local`` /
    ``get_admin_session_local`` from ``shu.core.database``.
    """

    def __init__(
        self,
        *,
        app_session_local: async_sessionmaker[AsyncSession],
        admin_session_local: async_sessionmaker[AsyncSession],
        audit_logger: AuditLogger,
    ) -> None:
        self._app_session_local = app_session_local
        self._admin_session_local = admin_session_local
        self._audit = audit_logger

    async def _emit_exit_audit(
        self,
        *,
        event_prefix: str,
        actor_user_id: str,
        target: str | None,
        body_exc: BaseException | None,
    ) -> None:
        """Emit the close/aborted audit, swallowing failures when the body raised.

        Two failure modes have to be handled differently:

        * Body succeeded → emit ``*_close``. If THIS audit raises, propagate —
          the operator needs to know the close record didn't make it.
        * Body raised → emit ``*_aborted`` with the error class so the trail
          captures the failure. If THIS audit raises, log loudly and swallow —
          we want the original ``body_exc`` to be the visible failure, not the
          audit-transport error masking it.
        """
        audit_kwargs: dict[str, Any] = {
            "event": f"{event_prefix}_close" if body_exc is None else f"{event_prefix}_aborted",
            "actor": actor_user_id,
            "target": target,
        }
        if body_exc is not None:
            audit_kwargs["error_class"] = type(body_exc).__name__

        try:
            await self._audit.log(**audit_kwargs)
        except Exception:
            if body_exc is None:
                # Clean-exit audit failure surfaces to the caller — same
                # contract as the open-audit failure (fail-closed on audit).
                raise
            logger.exception(
                "audit_emit_failed_during_unwind",
                extra={
                    "event_prefix": event_prefix,
                    "actor": actor_user_id,
                    "original_exception": type(body_exc).__name__,
                },
            )

    @asynccontextmanager
    async def impersonate_tenant(
        self,
        target_tenant_id: str,
        actor_user_id: str,
        reason: str,
    ) -> AsyncIterator[AsyncSession]:
        """Open a ``shu_app`` session scoped to ``target_tenant_id``.

        The session inherits RLS enforcement from ``shu_app``; the
        engine-level ``begin`` hook reads ``tenant_context`` and stamps
        ``app.tenant_id`` via ``set_config(..., true)`` on every transaction,
        so the impersonation persists for the session's lifetime.
        """
        # Audit FIRST. If this raises we never open the session — fail-closed.
        await self._audit.log(
            event="impersonate_tenant_open",
            actor=actor_user_id,
            target=target_tenant_id,
            reason=reason,
        )

        body_exc: BaseException | None = None
        try:
            async with tenant_context_for_tenant_id(target_tenant_id), self._app_session_local() as session:
                try:
                    yield session
                except BaseException as exc:
                    body_exc = exc
                    raise
        finally:
            await self._emit_exit_audit(
                event_prefix="impersonate_tenant",
                actor_user_id=actor_user_id,
                target=target_tenant_id,
                body_exc=body_exc,
            )

    @asynccontextmanager
    async def cross_tenant_query(
        self,
        actor_user_id: str,
        reason: str,
    ) -> AsyncIterator[AsyncSession]:
        """Open a ``shu_admin`` session for queries that span tenants.

        Callers MUST add explicit ``WHERE tenant_id = ...`` predicates to
        scope; no policy filters their reads. ``app.tenant_id`` is
        intentionally not set — BYPASSRLS ignores it, so setting it would
        only mislead a reader of the SQL into thinking it does something.
        """
        await self._audit.log(
            event="cross_tenant_query_open",
            actor=actor_user_id,
            reason=reason,
        )

        body_exc: BaseException | None = None
        try:
            async with self._admin_session_local() as session:
                try:
                    yield session
                except BaseException as exc:
                    body_exc = exc
                    raise
        finally:
            await self._emit_exit_audit(
                event_prefix="cross_tenant_query",
                actor_user_id=actor_user_id,
                target=None,
                body_exc=body_exc,
            )
