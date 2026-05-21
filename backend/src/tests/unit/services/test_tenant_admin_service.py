"""Tests for shu.services.tenant_admin_service.

Coverage focus:
* Audit emission ordering (open BEFORE session yield; fail-closed if open
  audit raises).
* Tenant context is set during impersonation and cleared after.
* Cross-tenant path uses the admin session factory and does NOT set context.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.core.tenant import tenant_context
from shu.services.audit_logger import AuditLogEmitError
from shu.services.tenant_admin_service import TenantAdminService


def _stub_session_factory() -> tuple[MagicMock, MagicMock]:
    """Return (factory, session) with the async-context-manager dance wired up.

    The real ``async_sessionmaker`` returns an object that supports
    ``async with``; we mirror that with MagicMocks so the service code's
    ``async with self._app_session_local() as session`` works against the
    stub without standing up a real engine.
    """
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)
    return factory, session


@pytest.mark.asyncio
async def test_impersonate_tenant_sets_context_during_session() -> None:
    """Inside the contextmanager the tenant_context ContextVar must be the target."""
    app_factory, app_session = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

    # The autouse conftest fixture pre-sets tenant_context to a default
    # value, so we snapshot it and assert restoration relative to that
    # rather than to None.
    pre_context = tenant_context.get(None)
    async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket 7") as session:
        assert tenant_context.get(None) == "tenant-X"
        assert session is app_session
    # Reset on exit — otherwise a subsequent admin operation would inherit it.
    assert tenant_context.get(None) == pre_context


@pytest.mark.asyncio
async def test_impersonate_tenant_audits_open_before_session_opens() -> None:
    """If the open audit raises, the session factory must not be called.

    This is the fail-closed contract: no cross-tenant work happens without
    a durable audit record.
    """
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()
    audit.log.side_effect = AuditLogEmitError("transport down")

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

    pre_context = tenant_context.get(None)
    with pytest.raises(AuditLogEmitError):
        async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket 7"):
            pass  # pragma: no cover - should never reach here

    app_factory.assert_not_called()
    # Context must NOT leak past the failed open — the contextmanager that
    # would have set it never got entered.
    assert tenant_context.get(None) == pre_context


@pytest.mark.asyncio
async def test_impersonate_tenant_emits_open_and_close_records() -> None:
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

    async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket 7"):
        pass

    open_call, close_call = audit.log.call_args_list
    open_kwargs: dict[str, Any] = open_call.kwargs
    assert open_kwargs["event"] == "impersonate_tenant_open"
    assert open_kwargs["target"] == "tenant-X"
    assert open_kwargs["actor"] == "actor-1"
    assert open_kwargs["reason"] == "ticket 7"
    close_kwargs: dict[str, Any] = close_call.kwargs
    assert close_kwargs["event"] == "impersonate_tenant_close"
    assert close_kwargs["actor"] == "actor-1"


@pytest.mark.asyncio
async def test_cross_tenant_query_uses_admin_factory_and_no_context() -> None:
    """The cross-tenant path must NOT set tenant_context — BYPASSRLS ignores it
    and setting it would mislead a reader of the SQL into thinking it scopes."""
    app_factory, _ = _stub_session_factory()
    admin_factory, admin_session = _stub_session_factory()
    audit = AsyncMock()

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

    # The cross-tenant path must not *change* the context — whatever the
    # caller had set going in remains set throughout, because BYPASSRLS
    # makes the value irrelevant for the query semantics. We assert the
    # path leaves the pre-existing value untouched rather than asserting
    # None (the conftest pre-populates context for every test).
    pre_context = tenant_context.get(None)
    async with svc.cross_tenant_query("actor-1", "usage report Q1") as session:
        assert session is admin_session
        assert tenant_context.get(None) == pre_context

    app_factory.assert_not_called()
    admin_factory.assert_called_once()


@pytest.mark.asyncio
async def test_cross_tenant_query_audits_open_before_session_opens() -> None:
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()
    audit.log.side_effect = AuditLogEmitError("transport down")

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

    with pytest.raises(AuditLogEmitError):
        async with svc.cross_tenant_query("actor-1", "usage report Q1"):
            pass  # pragma: no cover

    admin_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Exit-audit coverage
#
# Every open audit must be matched by a close-or-aborted exit audit, even
# when the caller's body raises. And when audit emission itself fails
# during unwind, the caller's original exception must surface — the
# audit-transport error must not mask it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_impersonate_tenant_emits_aborted_event_when_body_raises() -> None:
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

    class _CustomError(RuntimeError):
        pass

    with pytest.raises(_CustomError):
        async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket 7"):
            raise _CustomError("simulated failure in body")

    open_call, exit_call = audit.log.call_args_list
    assert open_call.kwargs["event"] == "impersonate_tenant_open"
    assert exit_call.kwargs["event"] == "impersonate_tenant_aborted"
    assert exit_call.kwargs["error_class"] == "_CustomError"
    # Target carried through so the audit trail can be filtered by tenant.
    assert exit_call.kwargs["target"] == "tenant-X"


@pytest.mark.asyncio
async def test_cross_tenant_query_emits_aborted_event_when_body_raises() -> None:
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

    class _CustomError(RuntimeError):
        pass

    with pytest.raises(_CustomError):
        async with svc.cross_tenant_query("actor-1", "usage report"):
            raise _CustomError("simulated failure")

    open_call, exit_call = audit.log.call_args_list
    assert open_call.kwargs["event"] == "cross_tenant_query_open"
    assert exit_call.kwargs["event"] == "cross_tenant_query_aborted"
    assert exit_call.kwargs["error_class"] == "_CustomError"


@pytest.mark.asyncio
async def test_aborted_audit_failure_does_not_mask_original_exception() -> None:
    """When close-time audit itself raises while a caller exception is
    in flight, the caller's exception must be the one that surfaces —
    otherwise the operator chases the audit infra blip instead of the
    actual failure that triggered the cross-tenant rollback."""
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    # First call (open) succeeds; second call (aborted) raises.
    audit.log.side_effect = [None, AuditLogEmitError("transport hiccup on close")]

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

    class _OriginalError(RuntimeError):
        pass

    with pytest.raises(_OriginalError):
        async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket"):
            raise _OriginalError("the real reason this failed")
